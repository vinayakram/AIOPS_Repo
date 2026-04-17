"""
Tests for the Pipeline Orchestrator and Trace Storage.

Run with: pytest tests/test_orchestrator.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.orchestrator import (
    InvestigationRequest,
    InvestigationResponse,
    PipelineStep,
)


client = TestClient(app)


# ── Unit: Request Model ────────────────────────────────────────────────


class TestInvestigationRequestModel:
    def test_valid_request(self):
        req = InvestigationRequest(
            timestamp="2026-04-10T04:19:55.204Z",
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.trace_id == "trace-abc-123"

    def test_missing_trace_id_raises(self):
        with pytest.raises(Exception):
            InvestigationRequest(
                timestamp="2026-04-10T04:19:55.204Z",
                agent_name="test",
            )

    def test_missing_timestamp_raises(self):
        with pytest.raises(Exception):
            InvestigationRequest(
                trace_id="trace-abc-123",
                agent_name="test",
            )

    def test_missing_agent_name_raises(self):
        with pytest.raises(Exception):
            InvestigationRequest(
                timestamp="2026-04-10T04:19:55.204Z",
                trace_id="trace-abc-123",
            )


# ── Unit: Response Models ──────────────────────────────────────────────


class TestInvestigationResponseModel:
    def test_pipeline_step(self):
        step = PipelineStep(
            agent="normalization", status="completed", processing_time_ms=123.4,
        )
        assert step.error is None

    def test_pipeline_step_with_error(self):
        step = PipelineStep(
            agent="correlation", status="failed",
            processing_time_ms=50.0, error="LLM returned invalid JSON",
        )
        assert step.status == "failed"

    def test_response_with_trace_id(self):
        resp = InvestigationResponse(
            trace_id="trace-abc-123",
            pipeline_steps=[
                PipelineStep(agent="normalization", status="completed", processing_time_ms=100.0),
            ],
            total_processing_time_ms=100.0,
            completed=True,
        )
        assert resp.trace_id == "trace-abc-123"
        assert resp.normalization is None
        assert resp.completed is True

    def test_full_pipeline_steps(self):
        steps = [
            PipelineStep(agent="normalization", status="completed", processing_time_ms=100.0),
            PipelineStep(agent="correlation", status="completed", processing_time_ms=200.0),
            PipelineStep(agent="error_analysis", status="completed", processing_time_ms=300.0),
            PipelineStep(agent="rca", status="completed", processing_time_ms=400.0),
            PipelineStep(agent="recommendation", status="completed", processing_time_ms=250.0),
        ]
        resp = InvestigationResponse(
            trace_id="trace-xyz",
            pipeline_steps=steps,
            total_processing_time_ms=1250.0,
            completed=True,
        )
        assert len(resp.pipeline_steps) == 5

    def test_partial_failure(self):
        steps = [
            PipelineStep(agent="normalization", status="completed", processing_time_ms=100.0),
            PipelineStep(agent="correlation", status="failed", processing_time_ms=50.0, error="Timeout"),
        ]
        resp = InvestigationResponse(
            trace_id="trace-fail",
            pipeline_steps=steps,
            total_processing_time_ms=150.0,
            completed=False,
        )
        assert resp.completed is False
        assert resp.pipeline_steps[1].error == "Timeout"


# ── Integration: API Endpoints ─────────────────────────────────────────


class TestInvestigateEndpoint:
    def test_rejects_empty_body(self):
        resp = client.post("/api/v1/investigate", json={})
        assert resp.status_code == 422

    def test_rejects_missing_trace_id(self):
        resp = client.post(
            "/api/v1/investigate",
            json={"timestamp": "2026-04-10T04:19:55.204Z", "agent_name": "test"},
        )
        assert resp.status_code == 422

    def test_rejects_missing_timestamp(self):
        resp = client.post(
            "/api/v1/investigate",
            json={"trace_id": "trace-abc", "agent_name": "test"},
        )
        assert resp.status_code == 422

    def test_rejects_missing_agent_name(self):
        resp = client.post(
            "/api/v1/investigate",
            json={"timestamp": "2026-04-10T04:19:55.204Z", "trace_id": "trace-abc"},
        )
        assert resp.status_code == 422

    def test_route_exists(self):
        routes = [r.path for r in app.routes]
        assert "/api/v1/investigate" in routes


class TestTracesEndpoint:
    def test_route_exists(self):
        routes = [r.path for r in app.routes]
        assert "/api/v1/traces/{trace_id}" in routes
        assert "/api/v1/traces" in routes

    def test_get_nonexistent_trace_returns_404(self):
        resp = client.get("/api/v1/traces/nonexistent-trace-id")
        assert resp.status_code == 404

    def test_list_traces_returns_empty(self):
        resp = client.get("/api/v1/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert "traces" in data
        assert "count" in data
