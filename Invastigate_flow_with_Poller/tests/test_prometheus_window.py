"""
Tests for dynamic Prometheus query window based on Langfuse trace span.

Scenarios covered
─────────────────
Unit — LangfuseClient.extract_timespan
  1.  Normal trace with latency_ms  → start + end computed from latency
  2.  latency_ms missing            → falls back to min/max of observation timestamps
  3.  Empty log list                → (None, None)
  4.  Single log, no latency        → start only (end = None fallback)
  5.  Zero latency trace            → start == end
  6.  20-second trace               → end = start + 20 000 ms
  7.  Timestamp with Z suffix       → parsed correctly

Unit — PrometheusClient._compute_time_range
  8.  trace_start + trace_end given → window = (start − buffer, end + buffer)
  9.  No trace span                 → window = (timestamp − buffer, timestamp + buffer)
  10. Custom buffer (60 s)          → correct delta applied
  11. Zero-length trace (start==end) → buffer expands on both sides
  12. Z-suffix timestamps           → parsed without error

Integration (mocked) — agent fetch orchestration
  13. Correlation: trace_id present   → Langfuse fetched BEFORE Prometheus; timespan forwarded
  14. Correlation: no trace_id        → Prometheus called with timestamp-only fallback
  15. Correlation: Langfuse fails     → Prometheus still called; uses timestamp fallback
  16. ErrorAnalysis: AGENT target     → Langfuse only; extract_timespan called
  17. ErrorAnalysis: INFRA target     → Prometheus only; timestamp fallback (no Langfuse)
  18. ErrorAnalysis: UNKNOWN target   → Langfuse first, timespan → Prometheus
  19. ErrorAnalysis: Langfuse fails   → Prometheus uses timestamp fallback
  20. RCA: AGENT target              → Langfuse only; extract_timespan called
  21. RCA: INFRA target              → Prometheus only; timestamp fallback
  22. RCA: UNKNOWN target            → Langfuse first, timespan → Prometheus
  23. RCA: Langfuse fails            → Prometheus uses timestamp fallback

Run with: pytest tests/test_prometheus_window.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.langfuse_client import LangfuseClient
from app.services.prometheus_client import PrometheusClient
from app.models.normalization import Entities, ErrorType, NormalizedIncident
from app.models.correlation import AnalysisDomain, CorrelationRequest
from app.models.error_analysis import (
    ErrorAnalysisResult,
    ErrorCategory,
    ErrorDetail,
    ErrorSeverity,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

TRACE_START = "2026-04-10T04:19:55.000+00:00"
TRACE_START_Z = "2026-04-10T04:19:55.000Z"
TIMESTAMP = "2026-04-10T04:19:55.204Z"


def _dt(iso: str) -> datetime:
    ts = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _make_langfuse_logs(
    start: str = TRACE_START,
    latency_ms: float | None = 5000.0,
    extra_obs: list[dict] | None = None,
) -> list[dict]:
    """Minimal Langfuse log list matching the structure produced by LangfuseClient."""
    trace_entry = {
        "timestamp": start,
        "source": "langfuse",
        "service": "test-agent",
        "message": "Trace 'test-agent'",
        "level": "INFO",
        "metadata": {
            "trace_id": "trace-abc",
            "latency_ms": latency_ms,
        },
    }
    logs = [trace_entry]
    if extra_obs:
        logs.extend(extra_obs)
    return logs


def _make_incident(**overrides) -> NormalizedIncident:
    defaults = dict(
        error_type=ErrorType.AI_AGENT,
        error_summary="LLM access disabled",
        timestamp=TIMESTAMP,
        confidence=0.9,
        entities=Entities(agent_id="test-agent", service="openai_gen", trace_id=None),
        signals=["llm_error"],
    )
    defaults.update(overrides)
    return NormalizedIncident(**defaults)


def _make_error_analysis(target: AnalysisDomain = AnalysisDomain.AGENT) -> ErrorAnalysisResult:
    return ErrorAnalysisResult(
        analysis_summary="LLM access disabled",
        analysis_target=target,
        errors=[
            ErrorDetail(
                error_id="ERR-001",
                category=ErrorCategory.LLM_FAILURE,
                severity=ErrorSeverity.CRITICAL,
                component="openai_gen",
                error_message="LLM access is disabled",
                timestamp=TIMESTAMP,
                evidence="status: ERROR",
                source="langfuse",
            )
        ],
        error_patterns=[],
        error_impacts=[],
        error_propagation_path=["openai_gen"],
        confidence=0.9,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Unit — LangfuseClient.extract_timespan
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractTimespan:
    """Scenario 1-7: unit tests for LangfuseClient.extract_timespan."""

    def test_normal_trace_with_latency(self):
        """Scenario 1: latency_ms present → end = start + latency."""
        logs = _make_langfuse_logs(start=TRACE_START, latency_ms=5000.0)
        start, end = LangfuseClient.extract_timespan(logs)

        dt_start = _dt(TRACE_START)
        dt_end = _dt(end)
        assert dt_end - dt_start == timedelta(milliseconds=5000)
        assert start == TRACE_START

    def test_missing_latency_falls_back_to_min_max(self):
        """Scenario 2: no latency_ms → min/max of all observation timestamps."""
        obs1 = {"timestamp": "2026-04-10T04:19:56.000+00:00", "source": "langfuse",
                 "service": "obs1", "message": "", "level": "INFO", "metadata": {}}
        obs2 = {"timestamp": "2026-04-10T04:20:10.000+00:00", "source": "langfuse",
                 "service": "obs2", "message": "", "level": "INFO", "metadata": {}}
        logs = _make_langfuse_logs(start=TRACE_START, latency_ms=None, extra_obs=[obs1, obs2])

        start, end = LangfuseClient.extract_timespan(logs)
        assert start == TRACE_START                        # min timestamp
        assert end == "2026-04-10T04:20:10.000+00:00"     # max timestamp

    def test_empty_logs_returns_none_none(self):
        """Scenario 3: empty log list → (None, None)."""
        start, end = LangfuseClient.extract_timespan([])
        assert start is None
        assert end is None

    def test_single_log_no_latency(self):
        """Scenario 4: one entry, latency_ms absent → start set, end from fallback."""
        logs = _make_langfuse_logs(latency_ms=None)
        start, end = LangfuseClient.extract_timespan(logs)
        # Only one timestamp → min == max == start
        assert start is not None
        assert end == start

    def test_zero_latency(self):
        """Scenario 5: latency_ms = 0 → start and end represent the same instant."""
        logs = _make_langfuse_logs(latency_ms=0)
        start, end = LangfuseClient.extract_timespan(logs)
        # Compare as datetimes — isoformat() may differ in sub-second representation
        assert _dt(start) == _dt(end)

    def test_twenty_second_trace(self):
        """Scenario 6: 20-second trace → end exactly 20 000 ms after start."""
        logs = _make_langfuse_logs(start=TRACE_START, latency_ms=20_000.0)
        start, end = LangfuseClient.extract_timespan(logs)

        dt_start = _dt(start)
        dt_end = _dt(end)
        assert (dt_end - dt_start).total_seconds() == pytest.approx(20.0)

    def test_z_suffix_timestamp_parsed(self):
        """Scenario 7: Z-suffix start timestamp is handled without error."""
        logs = _make_langfuse_logs(start=TRACE_START_Z, latency_ms=3000.0)
        start, end = LangfuseClient.extract_timespan(logs)
        assert start == TRACE_START_Z
        assert end is not None


# ─────────────────────────────────────────────────────────────────────────────
# Unit — PrometheusClient._compute_time_range
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeTimeRange:
    """Scenario 8-12: unit tests for PrometheusClient._compute_time_range."""

    def test_trace_span_mode(self):
        """Scenario 8: trace_start + trace_end → buffer applied on both sides."""
        trace_start = "2026-04-10T04:19:55.000+00:00"
        trace_end = "2026-04-10T04:20:15.000+00:00"   # 20 seconds later
        buffer = 300  # 5 min

        start, end = PrometheusClient._compute_time_range(
            timestamp=TIMESTAMP,
            buffer_seconds=buffer,
            trace_start=trace_start,
            trace_end=trace_end,
        )

        expected_start = _dt(trace_start) - timedelta(seconds=buffer)
        expected_end = _dt(trace_end) + timedelta(seconds=buffer)

        assert _dt(start) == expected_start
        assert _dt(end) == expected_end

    def test_timestamp_fallback_mode(self):
        """Scenario 9: no trace span → symmetric buffer around timestamp."""
        buffer = 300
        start, end = PrometheusClient._compute_time_range(
            timestamp=TIMESTAMP,
            buffer_seconds=buffer,
        )
        anchor = _dt(TIMESTAMP)
        assert _dt(start) == anchor - timedelta(seconds=buffer)
        assert _dt(end) == anchor + timedelta(seconds=buffer)

    def test_custom_buffer_60s(self):
        """Scenario 10: custom 60-second buffer."""
        trace_start = "2026-04-10T04:19:55.000+00:00"
        trace_end = "2026-04-10T04:20:05.000+00:00"
        buffer = 60

        start, end = PrometheusClient._compute_time_range(
            timestamp=TIMESTAMP,
            buffer_seconds=buffer,
            trace_start=trace_start,
            trace_end=trace_end,
        )
        assert (_dt(trace_start) - _dt(start)).total_seconds() == pytest.approx(buffer)
        assert (_dt(end) - _dt(trace_end)).total_seconds() == pytest.approx(buffer)

    def test_zero_length_trace_still_expands(self):
        """Scenario 11: start == end (instant trace) → buffer expands on both sides."""
        instant = "2026-04-10T04:19:55.000+00:00"
        buffer = 300

        start, end = PrometheusClient._compute_time_range(
            timestamp=TIMESTAMP,
            buffer_seconds=buffer,
            trace_start=instant,
            trace_end=instant,
        )
        total_span_seconds = (_dt(end) - _dt(start)).total_seconds()
        assert total_span_seconds == pytest.approx(buffer * 2)

    def test_z_suffix_handled_in_trace_span(self):
        """Scenario 12: Z-suffix timestamps are parsed without error."""
        start, end = PrometheusClient._compute_time_range(
            timestamp=TIMESTAMP,
            buffer_seconds=300,
            trace_start="2026-04-10T04:19:55.000Z",
            trace_end="2026-04-10T04:20:15.000Z",
        )
        assert start is not None
        assert end is not None
        assert _dt(end) > _dt(start)


# ─────────────────────────────────────────────────────────────────────────────
# Integration — Correlation Agent
# ─────────────────────────────────────────────────────────────────────────────


MOCK_LANGFUSE_LOGS = _make_langfuse_logs(start=TRACE_START, latency_ms=20_000.0)
MOCK_PROM_LOGS = [{"timestamp": TIMESTAMP, "source": "prometheus",
                   "service": "test-agent", "message": "up_status=1",
                   "level": "INFO", "metadata": {}}]


class TestCorrelationAgentWindow:
    """Scenarios 13-15: Correlation agent fetch ordering and window propagation."""

    def _make_request(self, trace_id=None):
        return CorrelationRequest(
            incident=_make_incident(),
            trace_id=trace_id,
            agent_name="test-agent",
        )

    @pytest.mark.asyncio
    async def test_trace_id_present_langfuse_called_first_timespan_forwarded(self):
        """Scenario 13: trace_id present → Langfuse fetched before Prometheus; timespan passed."""
        from app.agents.correlation_agent import CorrelationAgent

        call_order = []

        async def mock_fetch_trace(trace_id):
            call_order.append("langfuse")
            return MOCK_LANGFUSE_LOGS

        async def mock_fetch_metrics(timestamp, agent_name, trace_start=None, trace_end=None):
            call_order.append("prometheus")
            # trace_start and trace_end must be set from the Langfuse trace
            assert trace_start is not None, "trace_start must be forwarded from Langfuse"
            assert trace_end is not None, "trace_end must be forwarded from Langfuse"
            # Window must span the 20-second trace + buffer
            assert _dt(trace_end) > _dt(trace_start)
            return MOCK_PROM_LOGS

        agent = CorrelationAgent.__new__(CorrelationAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._langfuse.extract_timespan = LangfuseClient.extract_timespan
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_all_logs(self._make_request(trace_id="trace-abc"))

        assert call_order == ["langfuse", "prometheus"], (
            "Langfuse must be fetched BEFORE Prometheus when trace_id is present"
        )
        assert "langfuse" in sources
        assert "prometheus" in sources

    @pytest.mark.asyncio
    async def test_no_trace_id_prometheus_uses_timestamp_fallback(self):
        """Scenario 14: no trace_id → Prometheus called with no trace span (fallback)."""
        from app.agents.correlation_agent import CorrelationAgent

        async def mock_fetch_metrics(timestamp, agent_name, trace_start=None, trace_end=None):
            assert trace_start is None, "No Langfuse data → trace_start must be None"
            assert trace_end is None, "No Langfuse data → trace_end must be None"
            return MOCK_PROM_LOGS

        agent = CorrelationAgent.__new__(CorrelationAgent)
        agent._langfuse = MagicMock()
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_all_logs(self._make_request(trace_id=None))

        assert "prometheus" in sources
        assert "langfuse" not in sources

    @pytest.mark.asyncio
    async def test_langfuse_fails_prometheus_uses_timestamp_fallback(self):
        """Scenario 15: Langfuse throws → Prometheus still runs with timestamp fallback."""
        from app.agents.correlation_agent import CorrelationAgent

        async def mock_fetch_trace(_):
            raise ConnectionError("Langfuse unreachable")

        async def mock_fetch_metrics(timestamp, agent_name, trace_start=None, trace_end=None):
            # Langfuse failed → no trace span available → fallback
            assert trace_start is None
            assert trace_end is None
            return MOCK_PROM_LOGS

        agent = CorrelationAgent.__new__(CorrelationAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._langfuse.extract_timespan = LangfuseClient.extract_timespan
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_all_logs(self._make_request(trace_id="trace-abc"))

        assert "prometheus" in sources
        assert "langfuse" in sources  # WARN placeholder added


# ─────────────────────────────────────────────────────────────────────────────
# Integration — Error Analysis Agent
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorAnalysisAgentWindow:
    """Scenarios 16-19: Error Analysis agent window propagation."""

    @pytest.mark.asyncio
    async def test_agent_target_langfuse_only_no_prometheus(self):
        """Scenario 16: AGENT target → only Langfuse fetched; no Prometheus call."""
        from app.agents.error_analysis_agent import ErrorAnalysisAgent

        prometheus_called = []

        async def mock_fetch_trace(_):
            return MOCK_LANGFUSE_LOGS

        async def mock_fetch_metrics(**kwargs):
            prometheus_called.append(True)
            return MOCK_PROM_LOGS

        agent = ErrorAnalysisAgent.__new__(ErrorAnalysisAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._langfuse.extract_timespan = LangfuseClient.extract_timespan
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_logs_by_target(
            analysis_target=AnalysisDomain.AGENT,
            trace_id="trace-abc",
            timestamp=TIMESTAMP,
            agent_name="test-agent",
        )

        assert not prometheus_called, "AGENT target must not call Prometheus"
        assert "langfuse" in sources

    @pytest.mark.asyncio
    async def test_infra_target_prometheus_only_no_langfuse(self):
        """Scenario 17: INFRA target → only Prometheus; trace_start/end are None (no Langfuse)."""
        from app.agents.error_analysis_agent import ErrorAnalysisAgent

        langfuse_called = []

        async def mock_fetch_trace(_):
            langfuse_called.append(True)
            return MOCK_LANGFUSE_LOGS

        async def mock_fetch_metrics(timestamp, agent_name, trace_start=None, trace_end=None):
            assert trace_start is None, "INFRA target → no Langfuse → trace_start must be None"
            assert trace_end is None
            return MOCK_PROM_LOGS

        agent = ErrorAnalysisAgent.__new__(ErrorAnalysisAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_logs_by_target(
            analysis_target=AnalysisDomain.INFRA_LOGS,
            trace_id="trace-abc",
            timestamp=TIMESTAMP,
            agent_name="test-agent",
        )

        assert not langfuse_called, "INFRA target must not call Langfuse"
        assert "prometheus" in sources

    @pytest.mark.asyncio
    async def test_unknown_target_langfuse_first_timespan_to_prometheus(self):
        """Scenario 18: UNKNOWN target → Langfuse first, extracted timespan forwarded to Prometheus."""
        from app.agents.error_analysis_agent import ErrorAnalysisAgent

        received_trace_start = []
        received_trace_end = []

        async def mock_fetch_trace(_):
            return MOCK_LANGFUSE_LOGS

        async def mock_fetch_metrics(timestamp, agent_name, trace_start=None, trace_end=None):
            received_trace_start.append(trace_start)
            received_trace_end.append(trace_end)
            return MOCK_PROM_LOGS

        agent = ErrorAnalysisAgent.__new__(ErrorAnalysisAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._langfuse.extract_timespan = LangfuseClient.extract_timespan
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        await agent._fetch_logs_by_target(
            analysis_target=AnalysisDomain.UNKNOWN,
            trace_id="trace-abc",
            timestamp=TIMESTAMP,
            agent_name="test-agent",
        )

        assert received_trace_start[0] is not None, "trace_start must be forwarded"
        assert received_trace_end[0] is not None, "trace_end must be forwarded"
        # Verify end is 20 seconds after start (matching latency_ms=20_000)
        duration = (_dt(received_trace_end[0]) - _dt(received_trace_start[0])).total_seconds()
        assert duration == pytest.approx(20.0)

    @pytest.mark.asyncio
    async def test_langfuse_fails_prometheus_uses_timestamp_fallback(self):
        """Scenario 19: UNKNOWN target, Langfuse fails → Prometheus uses timestamp fallback."""
        from app.agents.error_analysis_agent import ErrorAnalysisAgent

        async def mock_fetch_trace(_):
            raise RuntimeError("Langfuse down")

        async def mock_fetch_metrics(timestamp, agent_name, trace_start=None, trace_end=None):
            assert trace_start is None, "Langfuse failed → trace_start must be None"
            assert trace_end is None
            return MOCK_PROM_LOGS

        agent = ErrorAnalysisAgent.__new__(ErrorAnalysisAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._langfuse.extract_timespan = LangfuseClient.extract_timespan
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_logs_by_target(
            analysis_target=AnalysisDomain.UNKNOWN,
            trace_id="trace-abc",
            timestamp=TIMESTAMP,
            agent_name="test-agent",
        )

        assert "prometheus" in sources


# ─────────────────────────────────────────────────────────────────────────────
# Integration — RCA Agent
# ─────────────────────────────────────────────────────────────────────────────


class TestRCAAgentWindow:
    """Scenarios 20-23: RCA agent window propagation."""

    @pytest.mark.asyncio
    async def test_agent_target_langfuse_only(self):
        """Scenario 20: AGENT target → Langfuse fetched; extract_timespan called; no Prometheus."""
        from app.agents.rca_agent import RCAAgent

        prometheus_called = []
        timespan_called = []

        async def mock_fetch_trace(_):
            return MOCK_LANGFUSE_LOGS

        def mock_extract_timespan(logs):
            timespan_called.append(True)
            return LangfuseClient.extract_timespan(logs)

        async def mock_fetch_metrics(**kwargs):
            prometheus_called.append(True)
            return MOCK_PROM_LOGS

        agent = RCAAgent.__new__(RCAAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._langfuse.extract_timespan = mock_extract_timespan
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_logs_by_target(
            rca_target=AnalysisDomain.AGENT,
            trace_id="trace-abc",
            timestamp=TIMESTAMP,
            agent_name="test-agent",
        )

        assert not prometheus_called, "AGENT target must not call Prometheus"
        assert timespan_called, "extract_timespan must be called even for AGENT-only target"
        assert "langfuse" in sources

    @pytest.mark.asyncio
    async def test_infra_target_prometheus_only_timestamp_fallback(self):
        """Scenario 21: INFRA target → Prometheus only with timestamp fallback."""
        from app.agents.rca_agent import RCAAgent

        langfuse_called = []

        async def mock_fetch_trace(_):
            langfuse_called.append(True)
            return MOCK_LANGFUSE_LOGS

        async def mock_fetch_metrics(timestamp, agent_name, trace_start=None, trace_end=None):
            assert trace_start is None
            assert trace_end is None
            return MOCK_PROM_LOGS

        agent = RCAAgent.__new__(RCAAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_logs_by_target(
            rca_target=AnalysisDomain.INFRA_LOGS,
            trace_id=None,
            timestamp=TIMESTAMP,
            agent_name="test-agent",
        )

        assert not langfuse_called
        assert "prometheus" in sources

    @pytest.mark.asyncio
    async def test_unknown_target_trace_window_forwarded_to_prometheus(self):
        """Scenario 22: UNKNOWN target → Langfuse timespan forwarded to Prometheus."""
        from app.agents.rca_agent import RCAAgent

        received = {}

        async def mock_fetch_trace(_):
            return MOCK_LANGFUSE_LOGS

        async def mock_fetch_metrics(timestamp, agent_name, trace_start=None, trace_end=None):
            received["trace_start"] = trace_start
            received["trace_end"] = trace_end
            return MOCK_PROM_LOGS

        agent = RCAAgent.__new__(RCAAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._langfuse.extract_timespan = LangfuseClient.extract_timespan
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_logs_by_target(
            rca_target=AnalysisDomain.UNKNOWN,
            trace_id="trace-abc",
            timestamp=TIMESTAMP,
            agent_name="test-agent",
        )

        assert received.get("trace_start") is not None
        assert received.get("trace_end") is not None
        # Prometheus window must cover the full 20-second trace
        duration = (
            _dt(received["trace_end"]) - _dt(received["trace_start"])
        ).total_seconds()
        assert duration == pytest.approx(20.0)
        assert "langfuse" in sources
        assert "prometheus" in sources

    @pytest.mark.asyncio
    async def test_langfuse_fails_prometheus_falls_back_to_timestamp(self):
        """Scenario 23: UNKNOWN target, Langfuse fails → Prometheus uses timestamp fallback."""
        from app.agents.rca_agent import RCAAgent

        async def mock_fetch_trace(_):
            raise TimeoutError("Langfuse timed out")

        async def mock_fetch_metrics(timestamp, agent_name, trace_start=None, trace_end=None):
            assert trace_start is None, "Langfuse failed → trace_start must be None"
            assert trace_end is None
            return MOCK_PROM_LOGS

        agent = RCAAgent.__new__(RCAAgent)
        agent._langfuse = MagicMock()
        agent._langfuse.fetch_trace = mock_fetch_trace
        agent._langfuse.extract_timespan = LangfuseClient.extract_timespan
        agent._prometheus = MagicMock()
        agent._prometheus.fetch_metrics = mock_fetch_metrics

        logs, sources = await agent._fetch_logs_by_target(
            rca_target=AnalysisDomain.UNKNOWN,
            trace_id="trace-abc",
            timestamp=TIMESTAMP,
            agent_name="test-agent",
        )

        assert "prometheus" in sources
        assert "langfuse" in sources  # WARN placeholder was added
