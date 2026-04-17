"""
Tests for the Error Analysis Agent.

Run with: pytest tests/test_error_analysis.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.normalization import Entities, ErrorType, NormalizedIncident
from app.models.correlation import (
    AnalysisDomain,
    ComponentRole,
    CorrelationResult,
    PeerComponent,
    RootCauseCandidate,
    TimelineEvent,
)
from app.models.error_analysis import (
    ErrorAnalysisRequest,
    ErrorAnalysisResponse,
    ErrorAnalysisResult,
    ErrorCategory,
    ErrorDetail,
    ErrorImpact,
    ErrorPattern,
    ErrorSeverity,
)


client = TestClient(app)


# ── Helpers: build sample data ────────────────────────────────────────


def _make_incident(**overrides) -> NormalizedIncident:
    defaults = dict(
        error_type=ErrorType.AI_AGENT,
        error_summary="LLM access disabled in agent summarizer-v2",
        timestamp="2026-04-10T04:19:55.204Z",
        confidence=0.9,
        entities=Entities(
            agent_id="summarizer-v2",
            service="openai_generation",
            trace_id="trace-abc-123",
        ),
        signals=["llm_error", "disabled"],
    )
    defaults.update(overrides)
    return NormalizedIncident(**defaults)


def _make_correlation(**overrides) -> CorrelationResult:
    defaults = dict(
        correlation_chain=[
            "LLM access disabled → openai_generation failure → summarizer-v2 error"
        ],
        peer_components=[
            PeerComponent(
                component="openai_generation",
                role=ComponentRole.ROOT_UPSTREAM_FAILURE,
                evidence="LLM access disabled error in output",
            ),
        ],
        timeline=[
            TimelineEvent(
                timestamp="2026-04-10T04:19:57.962Z",
                event="openai_generation span failed",
                service="openai_generation",
            ),
        ],
        root_cause_candidate=RootCauseCandidate(
            component="openai_generation",
            confidence=0.92,
            reason="LLM access was disabled",
        ),
        analysis_target=AnalysisDomain.AGENT,
    )
    defaults.update(overrides)
    return CorrelationResult(**defaults)


# ── Unit: Request Model ────────────────────────────────────────────────


class TestErrorAnalysisRequestModel:
    def test_valid_with_trace_id(self):
        req = ErrorAnalysisRequest(
            correlation=_make_correlation(),
            incident=_make_incident(),
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.trace_id == "trace-abc-123"
        assert req.correlation.analysis_target == AnalysisDomain.AGENT

    def test_valid_without_trace_id(self):
        req = ErrorAnalysisRequest(
            correlation=_make_correlation(analysis_target=AnalysisDomain.INFRA_LOGS),
            incident=_make_incident(),
            agent_name="summarizer-v2",
        )
        assert req.trace_id is None
        assert req.correlation.analysis_target == AnalysisDomain.INFRA_LOGS

    def test_unknown_target_valid(self):
        req = ErrorAnalysisRequest(
            correlation=_make_correlation(analysis_target=AnalysisDomain.UNKNOWN),
            incident=_make_incident(),
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.correlation.analysis_target == AnalysisDomain.UNKNOWN

    def test_missing_correlation_raises(self):
        with pytest.raises(Exception):
            ErrorAnalysisRequest(
                incident=_make_incident(),
                agent_name="test",
            )

    def test_missing_incident_raises(self):
        with pytest.raises(Exception):
            ErrorAnalysisRequest(
                correlation=_make_correlation(),
                agent_name="test",
            )

    def test_missing_agent_name_raises(self):
        with pytest.raises(Exception):
            ErrorAnalysisRequest(
                correlation=_make_correlation(),
                incident=_make_incident(),
            )


# ── Unit: Response Models ──────────────────────────────────────────────


class TestErrorAnalysisResponseModels:
    def test_error_detail(self):
        ed = ErrorDetail(
            error_id="ERR-001",
            category=ErrorCategory.LLM_FAILURE,
            severity=ErrorSeverity.CRITICAL,
            component="openai_generation",
            error_message="LLM access disabled",
            timestamp="2026-04-10T04:19:57.962Z",
            evidence="GENERATION 'openai_generation' | status: ERROR | Error: LLM access disabled",
            source="langfuse",
        )
        assert ed.category == ErrorCategory.LLM_FAILURE
        assert ed.severity == ErrorSeverity.CRITICAL

    def test_error_pattern(self):
        ep = ErrorPattern(
            pattern_name="Repeated LLM Timeout",
            description="Multiple LLM calls timed out within 30 seconds",
            occurrence_count=3,
            affected_components=["openai_generation", "summarizer-v2"],
            error_ids=["ERR-001", "ERR-002", "ERR-003"],
        )
        assert ep.occurrence_count == 3
        assert len(ep.error_ids) == 3

    def test_error_pattern_min_occurrence(self):
        with pytest.raises(Exception):
            ErrorPattern(
                pattern_name="test",
                description="test",
                occurrence_count=0,
                affected_components=[],
                error_ids=[],
            )

    def test_error_impact(self):
        ei = ErrorImpact(
            affected_service="summarizer-v2",
            impact_description="Agent unable to generate summaries due to LLM failure",
            severity=ErrorSeverity.HIGH,
        )
        assert ei.severity == ErrorSeverity.HIGH

    def test_full_error_analysis_result(self):
        result = ErrorAnalysisResult(
            analysis_summary="LLM access was disabled causing complete agent failure",
            analysis_target=AnalysisDomain.AGENT,
            errors=[
                ErrorDetail(
                    error_id="ERR-001",
                    category=ErrorCategory.LLM_FAILURE,
                    severity=ErrorSeverity.CRITICAL,
                    component="openai_generation",
                    error_message="LLM access disabled",
                    timestamp="2026-04-10T04:19:57.962Z",
                    evidence="status: ERROR in Langfuse span",
                    source="langfuse",
                ),
                ErrorDetail(
                    error_id="ERR-002",
                    category=ErrorCategory.TOOL_CALL_FAILURE,
                    severity=ErrorSeverity.HIGH,
                    component="summarizer-v2",
                    error_message="Agent returned error output",
                    timestamp="2026-04-10T04:19:57.964Z",
                    evidence="Trace output contains error payload",
                    source="langfuse",
                ),
            ],
            error_patterns=[
                ErrorPattern(
                    pattern_name="Cascading LLM Failure",
                    description="LLM disabled error propagated to downstream agent",
                    occurrence_count=2,
                    affected_components=["openai_generation", "summarizer-v2"],
                    error_ids=["ERR-001", "ERR-002"],
                ),
            ],
            error_impacts=[
                ErrorImpact(
                    affected_service="summarizer-v2",
                    impact_description="Complete agent failure — no summaries generated",
                    severity=ErrorSeverity.CRITICAL,
                ),
            ],
            error_propagation_path=[
                "LLM access disabled → openai_generation ERROR → summarizer-v2 error output"
            ],
            confidence=0.92,
        )
        assert len(result.errors) == 2
        assert len(result.error_patterns) == 1
        assert len(result.error_impacts) == 1
        assert result.confidence == 0.92
        assert result.analysis_target == AnalysisDomain.AGENT

    def test_error_analysis_requires_at_least_one_error(self):
        with pytest.raises(Exception):
            ErrorAnalysisResult(
                analysis_summary="No errors found",
                analysis_target=AnalysisDomain.AGENT,
                errors=[],
                confidence=0.5,
            )

    def test_confidence_out_of_range(self):
        with pytest.raises(Exception):
            ErrorAnalysisResult(
                analysis_summary="test",
                analysis_target=AnalysisDomain.AGENT,
                errors=[
                    ErrorDetail(
                        error_id="ERR-001",
                        category=ErrorCategory.UNKNOWN,
                        severity=ErrorSeverity.LOW,
                        component="test",
                        error_message="test",
                        timestamp="2026-04-10T04:19:57.962Z",
                        evidence="test",
                        source="langfuse",
                    ),
                ],
                confidence=1.5,
            )

    def test_error_analysis_response_wrapper(self):
        result = ErrorAnalysisResult(
            analysis_summary="Infrastructure errors detected",
            analysis_target=AnalysisDomain.INFRA_LOGS,
            errors=[
                ErrorDetail(
                    error_id="ERR-001",
                    category=ErrorCategory.SERVICE_UNAVAILABLE,
                    severity=ErrorSeverity.HIGH,
                    component="api-gateway",
                    error_message="Service unavailable",
                    timestamp="2026-04-10T04:19:55.204Z",
                    evidence="up_status=0 in Prometheus",
                    source="prometheus",
                ),
            ],
            confidence=0.85,
        )
        resp = ErrorAnalysisResponse(
            analysis=result,
            rca_target=AnalysisDomain.INFRA_LOGS,
            data_sources=["prometheus"],
            total_logs_analyzed=18,
            processing_time_ms=3456.7,
        )
        assert resp.data_sources == ["prometheus"]
        assert resp.total_logs_analyzed == 18
        assert resp.rca_target == AnalysisDomain.INFRA_LOGS

    def test_json_schema_generation(self):
        schema = ErrorAnalysisResult.model_json_schema()
        assert "analysis_summary" in schema["properties"]
        assert "analysis_target" in schema["properties"]
        assert "errors" in schema["properties"]
        assert "error_patterns" in schema["properties"]
        assert "error_impacts" in schema["properties"]
        assert "error_propagation_path" in schema["properties"]
        assert "confidence" in schema["properties"]

    def test_all_error_categories(self):
        for cat in ErrorCategory:
            ed = ErrorDetail(
                error_id="ERR-001",
                category=cat,
                severity=ErrorSeverity.MEDIUM,
                component="test",
                error_message="test",
                timestamp="2026-04-10T04:19:57.962Z",
                evidence="test",
                source="langfuse",
            )
            assert ed.category == cat

    def test_all_error_severities(self):
        for sev in ErrorSeverity:
            ed = ErrorDetail(
                error_id="ERR-001",
                category=ErrorCategory.UNKNOWN,
                severity=sev,
                component="test",
                error_message="test",
                timestamp="2026-04-10T04:19:57.962Z",
                evidence="test",
                source="prometheus",
            )
            assert ed.severity == sev

    def test_all_analysis_domains(self):
        for domain in AnalysisDomain:
            result = ErrorAnalysisResult(
                analysis_summary="test",
                analysis_target=domain,
                errors=[
                    ErrorDetail(
                        error_id="ERR-001",
                        category=ErrorCategory.UNKNOWN,
                        severity=ErrorSeverity.LOW,
                        component="test",
                        error_message="test",
                        timestamp="2026-04-10T04:19:57.962Z",
                        evidence="test",
                        source="langfuse",
                    ),
                ],
                confidence=0.5,
            )
            assert result.analysis_target == domain


# ── Integration: API Endpoint ──────────────────────────────────────────


class TestErrorAnalysisEndpoint:
    def test_rejects_empty_body(self):
        resp = client.post("/api/v1/error-analysis", json={})
        assert resp.status_code == 422

    def test_rejects_missing_correlation(self):
        resp = client.post(
            "/api/v1/error-analysis",
            json={
                "incident": {
                    "error_type": "AI_AGENT",
                    "error_summary": "test",
                    "timestamp": "2026-04-10T04:19:55.204Z",
                    "confidence": 0.9,
                },
                "agent_name": "test",
            },
        )
        assert resp.status_code == 422

    def test_rejects_missing_incident(self):
        resp = client.post(
            "/api/v1/error-analysis",
            json={
                "correlation": {
                    "correlation_chain": ["A → B"],
                    "root_cause_candidate": {
                        "component": "A",
                        "confidence": 0.8,
                        "reason": "test",
                    },
                    "analysis_target": "Agent",
                },
                "agent_name": "test",
            },
        )
        assert resp.status_code == 422

    def test_rejects_missing_agent_name(self):
        resp = client.post(
            "/api/v1/error-analysis",
            json={
                "correlation": {
                    "correlation_chain": ["A → B"],
                    "root_cause_candidate": {
                        "component": "A",
                        "confidence": 0.8,
                        "reason": "test",
                    },
                    "analysis_target": "Agent",
                },
                "incident": {
                    "error_type": "AI_AGENT",
                    "error_summary": "test",
                    "timestamp": "2026-04-10T04:19:55.204Z",
                    "confidence": 0.9,
                },
            },
        )
        assert resp.status_code == 422

    def test_accepts_valid_payload_agent_target(self):
        """Shape validation only — LLM call requires API key."""
        req = ErrorAnalysisRequest(
            correlation=_make_correlation(analysis_target=AnalysisDomain.AGENT),
            incident=_make_incident(),
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.correlation.analysis_target == AnalysisDomain.AGENT

    def test_accepts_valid_payload_infra_target(self):
        req = ErrorAnalysisRequest(
            correlation=_make_correlation(analysis_target=AnalysisDomain.INFRA_LOGS),
            incident=_make_incident(error_type=ErrorType.INFRA),
            agent_name="summarizer-v2",
        )
        assert req.correlation.analysis_target == AnalysisDomain.INFRA_LOGS
        assert req.trace_id is None

    def test_accepts_valid_payload_unknown_target(self):
        req = ErrorAnalysisRequest(
            correlation=_make_correlation(analysis_target=AnalysisDomain.UNKNOWN),
            incident=_make_incident(error_type=ErrorType.UNKNOWN),
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.correlation.analysis_target == AnalysisDomain.UNKNOWN
