"""
Unit tests for server/engine/issue_detector.py

Each NFR rule must have at least one test that:
  1. Seeds the DB with data that SHOULD trigger the rule
  2. Calls detect_issues() and asserts the correct issue is created
  3. Has a companion test that seeds data that should NOT trigger the rule

Coverage target: ≥ 90% (see CLAUDE.md §4b)
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from server.database.models import Trace, Span, Issue
from server.engine.issue_detector import detect_issues


@pytest.fixture(autouse=True)
def patch_psutil():
    """Patch psutil so infrastructure detectors don't read the real host."""
    with patch("server.engine.issue_detector.psutil") as mock_ps:
        mock_ps.cpu_percent.return_value = 20.0     # safe default
        mock_ps.virtual_memory.return_value.percent = 40.0
        mock_ps.disk_usage.return_value.percent = 30.0
        yield mock_ps


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trace(
    db,
    trace_id: str,
    app_name: str = "test-app",
    status: str = "ok",
    duration_ms: float = 300.0,
    offset_minutes: int = 0,
    input_preview: str = None,
    output_preview: str = None,
):
    now = datetime.utcnow() - timedelta(minutes=offset_minutes)
    t = Trace(
        id=trace_id,
        app_name=app_name,
        status=status,
        started_at=now,
        ended_at=now,
        total_duration_ms=duration_ms,
        input_preview=input_preview,
        output_preview=output_preview,
    )
    db.add(t)
    db.commit()
    return t


def _make_span(
    db,
    span_id: str,
    trace_id: str,
    name: str = "llm_call",
    span_type: str = "llm",
    status: str = "ok",
    duration_ms: float = 200.0,
    error_message: str = None,
):
    s = Span(
        id=span_id,
        trace_id=trace_id,
        name=name,
        span_type=span_type,
        status=status,
        started_at=datetime.utcnow(),
        ended_at=datetime.utcnow(),
        duration_ms=duration_ms,
        error_message=error_message,
    )
    db.add(s)
    db.commit()
    return s


# ── detect_issues returns a list ──────────────────────────────────────────────

class TestDetectIssuesReturnType:
    def test_returns_list_on_empty_db(self, db_session):
        result = detect_issues(db_session)
        assert isinstance(result, list)

    def test_no_issues_when_all_traces_ok(self, db_session):
        for i in range(5):
            _make_trace(db_session, f"t{i}", status="ok", duration_ms=100.0)
        result = detect_issues(db_session)
        assert result == []


# ── NFR-2 / NFR-5: Consecutive failure detection ──────────────────────────────

class TestConsecutiveTraceFailures:
    def test_three_consecutive_errors_create_issue(self, db_session):
        for i in range(3):
            _make_trace(db_session, f"t{i}", status="error")
        issues = detect_issues(db_session)
        issue_types = [i.issue_type for i in issues]
        assert any("consecutive" in t or "failure" in t for t in issue_types)

    def test_two_errors_do_not_trigger(self, db_session):
        for i in range(2):
            _make_trace(db_session, f"t{i}", status="error")
        _make_trace(db_session, "t3", status="ok")
        issues = detect_issues(db_session)
        consecutive_issues = [
            i for i in issues if "consecutive" in i.issue_type or "failure" in i.issue_type
        ]
        assert consecutive_issues == []

    def test_issue_not_duplicated_on_second_run(self, db_session):
        for i in range(3):
            _make_trace(db_session, f"t{i}", status="error")
        first_run = detect_issues(db_session)
        second_run = detect_issues(db_session)
        # Same fingerprint → second run should not create a duplicate
        assert len(second_run) == 0 or all(
            i.fingerprint not in {x.fingerprint for x in first_run} for i in second_run
        )


# ── NFR-8 / NFR-8a: HTTP error rate ──────────────────────────────────────────

class TestHttpErrorRate:
    def test_high_error_rate_in_window_creates_issue(self, db_session):
        # 8 errors, 2 ok = 80% error rate (well above any threshold)
        for i in range(8):
            _make_trace(db_session, f"err-{i}", status="error", offset_minutes=2)
        for i in range(2):
            _make_trace(db_session, f"ok-{i}", status="ok", offset_minutes=2)
        issues = detect_issues(db_session)
        assert len(issues) > 0

    def test_low_error_rate_does_not_trigger(self, db_session):
        # 0 errors in 20 traces = 0% error rate — must not trigger any error-rate rule
        for i in range(20):
            _make_trace(db_session, f"ok-{i}", status="ok", offset_minutes=2)
        issues = detect_issues(db_session)
        error_rate_issues = [
            i for i in issues
            if "error_rate" in i.issue_type or "http" in i.issue_type.lower()
        ]
        assert error_rate_issues == []


# ── NFR-11 / NFR-11a: CPU utilisation ────────────────────────────────────────

class TestCpuDetection:
    def test_high_cpu_creates_critical_issue(self, db_session, patch_psutil):
        patch_psutil.cpu_percent.return_value = 96.0  # above SEV1 threshold
        issues = detect_issues(db_session)
        cpu_issues = [i for i in issues if "cpu" in i.issue_type.lower()]
        assert len(cpu_issues) >= 1
        assert any(i.severity in ("critical", "high") for i in cpu_issues)

    def test_normal_cpu_no_issue(self, db_session, patch_psutil):
        patch_psutil.cpu_percent.return_value = 20.0
        issues = detect_issues(db_session)
        cpu_issues = [i for i in issues if "cpu" in i.issue_type.lower()]
        assert cpu_issues == []


# ── NFR-12 / NFR-13: Memory utilisation ──────────────────────────────────────

class TestMemoryDetection:
    def test_high_memory_creates_issue(self, db_session, patch_psutil):
        patch_psutil.virtual_memory.return_value.percent = 95.0
        issues = detect_issues(db_session)
        mem_issues = [i for i in issues if "mem" in i.issue_type.lower()]
        assert len(mem_issues) >= 1

    def test_normal_memory_no_issue(self, db_session, patch_psutil):
        patch_psutil.virtual_memory.return_value.percent = 40.0
        issues = detect_issues(db_session)
        mem_issues = [i for i in issues if "mem" in i.issue_type.lower()]
        assert mem_issues == []


# ── NFR-26: Token spike ───────────────────────────────────────────────────────

class TestTokenSpike:
    def test_token_spike_creates_issue(self, db_session):
        # Baseline spans dated 10 days ago (falls in "last week" bucket: 7-14 days ago)
        # with a low token count, then recent spans with a huge token count.
        last_week_offset = 10 * 24 * 60  # 10 days in minutes
        t = _make_trace(db_session, "t-baseline", offset_minutes=last_week_offset)
        _make_span(db_session, "s-base", "t-baseline", span_type="llm")
        s = db_session.query(Span).filter_by(id="s-base").first()
        s.started_at = datetime.utcnow() - timedelta(days=10)
        s.tokens_input = 100
        s.tokens_output = 100
        db_session.commit()

        # Recent spans (this week) with a massive token count — avg 50200 >> 1.5x baseline 200
        t_big = _make_trace(db_session, "t-big")
        _make_span(db_session, "s-big", "t-big", span_type="llm")
        sp_big = db_session.query(Span).filter_by(id="s-big").first()
        sp_big.tokens_input = 50000
        sp_big.tokens_output = 50000
        db_session.commit()

        issues = detect_issues(db_session)
        token_issues = [i for i in issues if "token" in i.issue_type.lower()]
        assert len(token_issues) >= 1


# ── Severity mapping ──────────────────────────────────────────────────────────

class TestSeverityMapping:
    def test_issue_severity_is_valid_value(self, db_session, patch_psutil):
        patch_psutil.cpu_percent.return_value = 96.0
        issues = detect_issues(db_session)
        valid = {"low", "medium", "high", "critical"}
        for issue in issues:
            assert issue.severity in valid

    def test_issue_has_fingerprint(self, db_session, patch_psutil):
        patch_psutil.cpu_percent.return_value = 96.0
        issues = detect_issues(db_session)
        for issue in issues:
            assert issue.fingerprint is not None
            assert len(issue.fingerprint) > 0


# ── NFR-29: Application-level error in trace output ───────────────────────────

class TestOutputErrors:
    """NFR-29: A trace with status='ok' but an error message in output_preview
    must still be detected and raise an issue."""

    def test_warning_emoji_in_output_creates_issue(self, db_session):
        """⚠️ prefix in output should be detected even when status='ok'."""
        _make_trace(
            db_session, "t1",
            app_name="medical-agent",
            status="ok",
            output_preview='{"answer_preview": "⚠️ Error generating response: Error code: 400"}',
        )
        issues = detect_issues(db_session)
        output_error_issues = [i for i in issues if i.issue_type == "nfr_output_error"]
        assert len(output_error_issues) >= 1
        assert output_error_issues[0].app_name == "medical-agent"

    def test_anthropic_credit_error_in_output_creates_issue(self, db_session):
        """Anthropic credit balance error in output should raise an issue."""
        _make_trace(
            db_session, "t1",
            app_name="medical-agent",
            status="ok",
            output_preview='{"answer_preview": "⚠️ Error generating response: Error code: 400 - credit balance is too low"}',
        )
        issues = detect_issues(db_session)
        output_error_issues = [i for i in issues if i.issue_type == "nfr_output_error"]
        assert len(output_error_issues) >= 1
        assert "credit" in output_error_issues[0].description.lower() or "400" in output_error_issues[0].description

    def test_api_error_code_in_output_creates_issue(self, db_session):
        """'Error code:' pattern in output detects API failures silently returned."""
        _make_trace(
            db_session, "t1",
            app_name="web-search-agent",
            status="ok",
            output_preview='{"result": "Error code: 429 - Rate limit exceeded"}',
        )
        issues = detect_issues(db_session)
        output_error_issues = [i for i in issues if i.issue_type == "nfr_output_error"]
        assert len(output_error_issues) >= 1

    def test_invalid_request_error_in_output_creates_issue(self, db_session):
        """JSON error type 'invalid_request_error' in output is detected."""
        _make_trace(
            db_session, "t1",
            app_name="medical-agent",
            status="ok",
            output_preview='{"error": {"type": "invalid_request_error", "message": "Bad request"}}',
        )
        issues = detect_issues(db_session)
        output_error_issues = [i for i in issues if i.issue_type == "nfr_output_error"]
        assert len(output_error_issues) >= 1

    def test_clean_output_does_not_trigger(self, db_session):
        """Normal successful output must not raise a false positive."""
        _make_trace(
            db_session, "t1",
            app_name="medical-agent",
            status="ok",
            output_preview='{"answer_preview": "Alzheimer disease is treated with cholinesterase inhibitors."}',
        )
        issues = detect_issues(db_session)
        output_error_issues = [i for i in issues if i.issue_type == "nfr_output_error"]
        assert output_error_issues == []

    def test_trace_with_no_output_does_not_trigger(self, db_session):
        """Traces with null output_preview must not raise an issue."""
        _make_trace(db_session, "t1", app_name="medical-agent", status="ok", output_preview=None)
        issues = detect_issues(db_session)
        output_error_issues = [i for i in issues if i.issue_type == "nfr_output_error"]
        assert output_error_issues == []

    def test_issue_not_duplicated_on_second_run(self, db_session):
        """Same output error on second detector run must not create a duplicate issue."""
        _make_trace(
            db_session, "t1",
            app_name="medical-agent",
            status="ok",
            output_preview='{"answer_preview": "⚠️ Error generating response: Error code: 400"}',
        )
        first_run = detect_issues(db_session)
        second_run = detect_issues(db_session)
        output_error_first = [i for i in first_run if i.issue_type == "nfr_output_error"]
        output_error_second = [i for i in second_run if i.issue_type == "nfr_output_error"]
        assert len(output_error_first) >= 1
        assert output_error_second == []  # deduplicated — no new issue


# ── NFR-31: Medical RAG LLM disabled burst ───────────────────────────────────

class TestLlmDisabledBurst:
    def test_three_disabled_llm_outputs_in_10_minutes_create_critical_issue(self, db_session):
        disabled_output = (
            "LLM access is currently disabled by admin. "
            "Please use source articles and scores below for analysis."
        )
        for i in range(3):
            _make_trace(
                db_session,
                f"llm-disabled-{i}",
                app_name="medical-rag",
                status="ok",
                offset_minutes=i,
                input_preview=f"medical query {i}",
                output_preview=disabled_output,
            )

        issues = detect_issues(db_session)
        llm_disabled_issues = [
            i for i in issues if i.issue_type == "nfr_llm_disabled_burst"
        ]
        assert len(llm_disabled_issues) == 1
        assert llm_disabled_issues[0].rule_id == "NFR-31"
        assert llm_disabled_issues[0].severity == "critical"
        assert llm_disabled_issues[0].trace_id == "llm-disabled-0"

    def test_two_disabled_llm_outputs_do_not_trigger(self, db_session):
        disabled_output = (
            "LLM access is currently disabled by admin. "
            "Please use source articles and scores below for analysis."
        )
        for i in range(2):
            _make_trace(
                db_session,
                f"llm-disabled-{i}",
                app_name="medical-rag",
                status="ok",
                offset_minutes=i,
                output_preview=disabled_output,
            )

        issues = detect_issues(db_session)
        llm_disabled_issues = [
            i for i in issues if i.issue_type == "nfr_llm_disabled_burst"
        ]
        assert llm_disabled_issues == []
