"""
Tests for the Correlation Agent.

Run with: pytest tests/test_correlation.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.normalization import Entities, ErrorType, NormalizedIncident
from app.models.correlation import (
    AnalysisDomain,
    ComponentRole,
    CorrelationRequest,
    CorrelationResponse,
    CorrelationResult,
    PeerComponent,
    RootCauseCandidate,
    TimelineEvent,
)


client = TestClient(app)


# ── Helper: build a sample normalized incident ─────────────────────────

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


# ── Unit: Request Model ────────────────────────────────────────────────


class TestCorrelationRequestModel:
    def test_valid_with_trace_id(self):
        req = CorrelationRequest(
            incident=_make_incident(),
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.trace_id == "trace-abc-123"
        assert req.incident.error_type == ErrorType.AI_AGENT

    def test_valid_without_trace_id(self):
        req = CorrelationRequest(
            incident=_make_incident(),
            agent_name="summarizer-v2",
        )
        assert req.trace_id is None

    def test_missing_incident_raises(self):
        with pytest.raises(Exception):
            CorrelationRequest(agent_name="test")

    def test_missing_agent_name_raises(self):
        with pytest.raises(Exception):
            CorrelationRequest(incident=_make_incident())


# ── Unit: Response Models ──────────────────────────────────────────────


class TestCorrelationResponseModels:
    def test_peer_component(self):
        pc = PeerComponent(
            component="proxy-server",
            role=ComponentRole.ROOT_UPSTREAM_FAILURE,
            evidence="DNS resolution failure detected first at proxy layer",
        )
        assert pc.role == ComponentRole.ROOT_UPSTREAM_FAILURE

    def test_timeline_event(self):
        te = TimelineEvent(
            timestamp="2026-04-10T04:19:55.204Z",
            event="LLM access disabled error returned",
            service="openai_generation",
        )
        assert te.service == "openai_generation"

    def test_root_cause_candidate(self):
        rcc = RootCauseCandidate(
            component="openai_generation",
            confidence=0.85,
            reason="LLM access was disabled, causing agent to fail",
        )
        assert rcc.confidence == 0.85

    def test_root_cause_confidence_out_of_range(self):
        with pytest.raises(Exception):
            RootCauseCandidate(
                component="test",
                confidence=1.5,
                reason="test",
            )

    def test_full_correlation_result(self):
        result = CorrelationResult(
            correlation_chain=[
                "LLM access disabled → openai_generation failure → medical-rag agent error"
            ],
            peer_components=[
                PeerComponent(
                    component="openai_generation",
                    role=ComponentRole.ROOT_UPSTREAM_FAILURE,
                    evidence="LLM access disabled error in output",
                ),
                PeerComponent(
                    component="medical-rag",
                    role=ComponentRole.CONTRIBUTING_FACTOR,
                    evidence="Agent returned error output downstream",
                ),
            ],
            timeline=[
                TimelineEvent(
                    timestamp="2026-04-10T04:19:57.962Z",
                    event="openai_generation span started and immediately failed",
                    service="openai_generation",
                ),
                TimelineEvent(
                    timestamp="2026-04-10T04:19:57.964Z",
                    event="medical-rag returned error in output",
                    service="medical-rag",
                ),
            ],
            root_cause_candidate=RootCauseCandidate(
                component="openai_generation",
                confidence=0.92,
                reason="LLM access was disabled, causing the entire agent pipeline to fail",
            ),
            analysis_target=AnalysisDomain.AGENT,
        )
        assert len(result.correlation_chain) == 1
        assert len(result.peer_components) == 2
        assert len(result.timeline) == 2
        assert result.root_cause_candidate.confidence == 0.92
        assert result.analysis_target == AnalysisDomain.AGENT

    def test_correlation_response_wrapper(self):
        result = CorrelationResult(
            correlation_chain=["A → B"],
            peer_components=[],
            timeline=[],
            root_cause_candidate=RootCauseCandidate(
                component="A", confidence=0.8, reason="First to fail"
            ),
            analysis_target=AnalysisDomain.INFRA_LOGS,
        )
        resp = CorrelationResponse(
            correlation=result,
            data_sources=["langfuse", "prometheus"],
            total_logs_analyzed=12,
            processing_time_ms=2345.6,
        )
        assert resp.data_sources == ["langfuse", "prometheus"]
        assert resp.total_logs_analyzed == 12

    def test_json_schema_generation(self):
        schema = CorrelationResult.model_json_schema()
        assert "correlation_chain" in schema["properties"]
        assert "peer_components" in schema["properties"]
        assert "timeline" in schema["properties"]
        assert "root_cause_candidate" in schema["properties"]
        assert "analysis_target" in schema["properties"]

    def test_all_component_roles(self):
        for role in ComponentRole:
            pc = PeerComponent(
                component="test",
                role=role,
                evidence="test evidence",
            )
            assert pc.role == role

    def test_all_analysis_domains(self):
        for domain in AnalysisDomain:
            result = CorrelationResult(
                correlation_chain=["A → B"],
                peer_components=[],
                timeline=[],
                root_cause_candidate=RootCauseCandidate(
                    component="A", confidence=0.5, reason="test"
                ),
                analysis_target=domain,
            )
            assert result.analysis_target == domain


# ── Integration: API Endpoint ──────────────────────────────────────────


class TestCorrelationEndpoint:
    def test_rejects_empty_body(self):
        resp = client.post("/api/v1/correlate", json={})
        assert resp.status_code == 422

    def test_rejects_missing_incident(self):
        resp = client.post(
            "/api/v1/correlate",
            json={"agent_name": "test"},
        )
        assert resp.status_code == 422

    def test_rejects_missing_agent_name(self):
        resp = client.post(
            "/api/v1/correlate",
            json={
                "incident": {
                    "error_type": "AI_AGENT",
                    "error_summary": "test",
                    "timestamp": "2026-04-10T04:19:55.204Z",
                    "confidence": 0.9,
                },
            },
        )
        assert resp.status_code == 422

    def test_accepts_valid_payload_with_trace_id(self):
        """Shape validation only — LLM call requires API key."""
        req = CorrelationRequest(
            incident=_make_incident(),
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.trace_id == "trace-abc-123"

    def test_accepts_valid_payload_without_trace_id(self):
        req = CorrelationRequest(
            incident=_make_incident(),
            agent_name="summarizer-v2",
        )
        assert req.trace_id is None

    def test_no_error_incident_still_valid_request(self):
        """Even NO_ERROR incidents can be sent for correlation."""
        req = CorrelationRequest(
            incident=_make_incident(
                error_type=ErrorType.NO_ERROR,
                error_summary="No error detected",
                confidence=1.0,
                signals=[],
            ),
            agent_name="summarizer-v2",
        )
        assert req.incident.error_type == ErrorType.NO_ERROR
