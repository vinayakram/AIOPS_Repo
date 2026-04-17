"""
Tests for the Normalization Agent.

Run with: pytest tests/ -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.agents.normalization_agent import _has_error_signals
from app.models.normalization import (
    DataSource,
    Entities,
    ErrorType,
    NormalizedIncident,
    NormalizationRequest,
    NormalizationResponse,
)
from app.services.langfuse_client import LangfuseClient
from app.services.prometheus_client import PrometheusClient


client = TestClient(app)


# ── Unit: Request Model ────────────────────────────────────────────────


class TestRequestModel:
    def test_valid_with_trace_id(self):
        """trace_id provided → Langfuse path."""
        req = NormalizationRequest(
            timestamp="2025-01-15T10:32:00Z",
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.trace_id == "trace-abc-123"
        assert req.agent_name == "summarizer-v2"

    def test_valid_without_trace_id(self):
        """trace_id absent → Prometheus path."""
        req = NormalizationRequest(
            timestamp="2025-01-15T10:32:00Z",
            agent_name="summarizer-v2",
        )
        assert req.trace_id is None
        assert req.timestamp == "2025-01-15T10:32:00Z"

    def test_trace_id_explicitly_null(self):
        req = NormalizationRequest(
            timestamp="2025-01-15T10:32:00Z",
            trace_id=None,
            agent_name="planner-v1",
        )
        assert req.trace_id is None

    def test_missing_timestamp_raises(self):
        with pytest.raises(Exception):
            NormalizationRequest(agent_name="summarizer-v2")

    def test_missing_agent_name_raises(self):
        with pytest.raises(Exception):
            NormalizationRequest(timestamp="2025-01-15T10:32:00Z")


# ── Unit: Response Model ───────────────────────────────────────────────


class TestResponseModel:
    def test_normalized_incident_valid(self):
        incident = NormalizedIncident(
            error_type=ErrorType.NETWORK,
            error_summary="DNS failure in proxy layer",
            timestamp="2025-01-15T10:32:00Z",
            confidence=0.9,
            entities=Entities(
                agent_id="summarizer-v2",
                service="proxy",
                trace_id="trace-abc-123",
            ),
            signals=["dns_failure", "timeout"],
        )
        assert incident.error_type == ErrorType.NETWORK
        assert len(incident.signals) == 2
        assert incident.entities.trace_id == "trace-abc-123"

    def test_no_error_type(self):
        incident = NormalizedIncident(
            error_type=ErrorType.NO_ERROR,
            error_summary="No error detected",
            timestamp="2025-01-15T10:32:00Z",
            confidence=1.0,
            entities=Entities(agent_id="summarizer-v2"),
            signals=[],
        )
        assert incident.error_type == ErrorType.NO_ERROR
        assert incident.error_summary == "No error detected"
        assert incident.confidence == 1.0
        assert incident.signals == []

    def test_confidence_out_of_range(self):
        with pytest.raises(Exception):
            NormalizedIncident(
                error_type=ErrorType.INFRA,
                error_summary="test",
                timestamp="2025-01-15T10:32:00Z",
                confidence=1.5,
            )

    def test_entities_defaults(self):
        incident = NormalizedIncident(
            error_type=ErrorType.UNKNOWN,
            error_summary="Unknown error",
            timestamp="2025-01-15T10:32:00Z",
            confidence=0.3,
        )
        assert incident.entities.agent_id is None
        assert incident.entities.service is None
        assert incident.entities.trace_id is None
        assert incident.signals == []

    def test_all_error_types_including_no_error(self):
        for et in ErrorType:
            incident = NormalizedIncident(
                error_type=et,
                error_summary=f"Test {et.value}",
                timestamp="2025-01-15T10:32:00Z",
                confidence=0.5,
            )
            assert incident.error_type == et

    def test_response_with_langfuse_source(self):
        incident = NormalizedIncident(
            error_type=ErrorType.AI_AGENT,
            error_summary="Agent crashed during LLM call",
            timestamp="2025-01-15T10:32:00Z",
            confidence=0.85,
            entities=Entities(agent_id="summarizer-v2"),
            signals=["llm_error", "timeout"],
        )
        resp = NormalizationResponse(
            incident=incident,
            data_source=DataSource.LANGFUSE,
            raw_log_count=5,
            processing_time_ms=142.5,
        )
        assert resp.data_source == DataSource.LANGFUSE
        assert resp.raw_log_count == 5

    def test_response_with_prometheus_source(self):
        incident = NormalizedIncident(
            error_type=ErrorType.INFRA,
            error_summary="Pod restarting with OOM",
            timestamp="2025-01-15T10:32:00Z",
            confidence=0.78,
            signals=["out_of_memory", "crash"],
        )
        resp = NormalizationResponse(
            incident=incident,
            data_source=DataSource.PROMETHEUS,
            raw_log_count=3,
            processing_time_ms=210.0,
        )
        assert resp.data_source == DataSource.PROMETHEUS

    def test_no_error_response(self):
        incident = NormalizedIncident(
            error_type=ErrorType.NO_ERROR,
            error_summary="No error detected",
            timestamp="2025-01-15T10:32:00Z",
            confidence=1.0,
            entities=Entities(agent_id="summarizer-v2", trace_id="trace-123"),
            signals=[],
        )
        resp = NormalizationResponse(
            incident=incident,
            data_source=DataSource.LANGFUSE,
            raw_log_count=4,
            processing_time_ms=5.2,
        )
        assert resp.incident.error_type == ErrorType.NO_ERROR
        assert resp.incident.signals == []
        assert resp.processing_time_ms == 5.2  # fast — no LLM call

    def test_json_schema_includes_no_error(self):
        schema = NormalizedIncident.model_json_schema()
        assert "error_type" in schema["properties"]
        # NO_ERROR should be in the enum values
        error_ref = schema["properties"]["error_type"]
        # Resolve $ref if present
        if "$ref" in error_ref:
            ref_key = error_ref["$ref"].split("/")[-1]
            enum_values = schema["$defs"][ref_key]["enum"]
        else:
            enum_values = error_ref.get("enum", [])
        assert "NO_ERROR" in enum_values


# ── Unit: Error Signal Detection ───────────────────────────────────────


class TestErrorSignalDetection:
    def test_detects_error_level(self):
        logs = [{"level": "ERROR", "message": "something happened"}]
        assert _has_error_signals(logs) is True

    def test_detects_warn_level(self):
        logs = [{"level": "WARN", "message": "something happened"}]
        assert _has_error_signals(logs) is True

    def test_detects_warning_level(self):
        logs = [{"level": "WARNING", "message": "something happened"}]
        assert _has_error_signals(logs) is True

    def test_detects_fatal_level(self):
        logs = [{"level": "FATAL", "message": "process died"}]
        assert _has_error_signals(logs) is True

    def test_detects_error_keyword_in_message(self):
        """Keyword scan activates when level is missing/ambiguous."""
        logs = [{"message": "DNS resolution failed after 3 retries"}]
        assert _has_error_signals(logs) is True

    def test_detects_timeout_keyword(self):
        logs = [{"message": "Request timeout after 5000ms"}]
        assert _has_error_signals(logs) is True

    def test_detects_crash_keyword(self):
        logs = [{"message": "Container crashed unexpectedly"}]
        assert _has_error_signals(logs) is True

    def test_no_error_in_clean_logs(self):
        logs = [
            {"level": "INFO", "message": "Request completed successfully in 120ms"},
            {"level": "INFO", "message": "Health check passed"},
            {"level": "DEBUG", "message": "Processing 42 items"},
        ]
        assert _has_error_signals(logs) is False

    def test_no_error_in_empty_logs(self):
        assert _has_error_signals([]) is False

    def test_no_error_with_missing_fields(self):
        logs = [
            {"message": "All good"},
            {"level": None, "message": "Still fine"},
        ]
        assert _has_error_signals(logs) is False

    def test_mixed_logs_detects_error(self):
        logs = [
            {"level": "INFO", "message": "Request completed successfully"},
            {"level": "INFO", "message": "Health check passed"},
            {"level": "ERROR", "message": "Connection refused to upstream"},
        ]
        assert _has_error_signals(logs) is True


# ── Unit: Service Clients ──────────────────────────────────────────────


class TestLangfuseClient:
    def test_trace_to_logs_transforms_correctly(self):
        trace = {
            "id": "trace-abc",
            "name": "summarizer-v2",
            "status": "ERROR",
            "startTime": "2025-01-15T10:32:00Z",
            "statusMessage": "LLM timeout",
            "sessionId": "sess-1",
            "userId": "user-1",
            "tags": ["prod"],
            "latency": 5200,
            "calculatedTotalCost": 0.003,
        }
        observations = [
            {
                "id": "obs-1",
                "type": "GENERATION",
                "name": "gpt4o-call",
                "status": "ERROR",
                "statusMessage": "Connection refused",
                "startTime": "2025-01-15T10:32:01Z",
                "model": "gpt-4o",
                "usage": {"input": 500, "output": 0},
                "latency": 5000,
                "calculatedTotalCost": 0.002,
            },
            {
                "id": "obs-2",
                "type": "SPAN",
                "name": "retrieval-step",
                "status": "OK",
                "startTime": "2025-01-15T10:31:58Z",
                "latency": 200,
            },
        ]
        logs = LangfuseClient._trace_to_logs(trace, observations)

        assert len(logs) == 3
        assert logs[0]["source"] == "langfuse"
        assert logs[0]["level"] == "ERROR"
        assert logs[0]["metadata"]["trace_id"] == "trace-abc"
        assert logs[1]["level"] == "ERROR"
        assert logs[2]["level"] == "INFO"

    def test_clean_trace_produces_no_error_signals(self):
        """A successful trace should have no error signals."""
        trace = {
            "id": "trace-ok",
            "name": "summarizer-v2",
            "status": "OK",
            "startTime": "2025-01-15T10:32:00Z",
            "latency": 300,
        }
        observations = [
            {
                "id": "obs-1",
                "type": "GENERATION",
                "name": "gpt4o-call",
                "status": "OK",
                "startTime": "2025-01-15T10:32:01Z",
                "model": "gpt-4o",
                "usage": {"input": 200, "output": 150},
                "latency": 250,
            },
        ]
        logs = LangfuseClient._trace_to_logs(trace, observations)
        assert _has_error_signals(logs) is False

    def test_detects_error_in_output_json_string(self):
        """
        Real Langfuse data: no 'status' field, error is embedded
        in the 'output' field as a JSON string with an "error" key.
        """
        trace = {
            "id": "589eff300d4e35ea",
            "name": "medical-rag (589eff30)",
            "startTime": "2026-04-10T04:19:55.204Z",
            "endTime": "2026-04-10T04:19:57.964Z",
            "input": '{"query": "Effect of metformin on diabetes"}',
            "output": '{"error": "LLM access is disabled (demo error mode)."}',
            "metadata": '{"user_id":"admin","tags":["medical-rag","pubmed"]}',
        }
        observations = [
            {
                "id": "63405331f595286d",
                "type": "SPAN",
                "name": "pubmed_fetch (63405331)",
                "startTime": "2026-04-10T04:19:55.204Z",
                "input": '{"query": "Effect of metformin on diabetes", "max_articles": 30}',
            },
            {
                "id": "8050c35096a244ba",
                "type": "SPAN",
                "name": "openai_generation (8050c350)",
                "startTime": "2026-04-10T04:19:57.962Z",
                "input": '{"model": "claude-opus-4-6", "articles_used": 5}',
            },
        ]
        logs = LangfuseClient._trace_to_logs(trace, observations)

        # Trace-level log should be ERROR because output has {"error": "..."}
        assert logs[0]["level"] == "ERROR"
        assert "LLM access is disabled" in logs[0]["message"]
        assert _has_error_signals(logs) is True

    def test_detects_error_without_status_field(self):
        """Observations with no status field but error in output."""
        trace = {
            "id": "trace-no-status",
            "name": "test-agent",
            "startTime": "2025-01-15T10:32:00Z",
            "output": '{"error": "Connection timeout after 30s"}',
        }
        observations = []
        logs = LangfuseClient._trace_to_logs(trace, observations)
        assert logs[0]["level"] == "ERROR"
        assert _has_error_signals(logs) is True


class TestPrometheusClient:
    def test_compute_time_range(self):
        start, end = PrometheusClient._compute_time_range("2025-01-15T10:32:00Z")
        assert "2025-01-15T10:27:00" in start
        assert "2025-01-15T10:37:00" in end

    def test_result_to_logs_empty(self):
        logs = PrometheusClient._result_to_logs(
            query_name="error_rate",
            description="HTTP 5xx error rate",
            promql="test",
            result={"resultType": "matrix", "result": []},
            agent_name="test-agent",
        )
        assert logs == []

    def test_result_to_logs_with_error(self):
        result = {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"job": "summarizer-v2", "status": "500"},
                    "values": [[1705312320.0, "0.15"]],
                }
            ],
        }
        logs = PrometheusClient._result_to_logs(
            query_name="error_rate",
            description="HTTP 5xx error rate",
            promql="test_query",
            result=result,
            agent_name="summarizer-v2",
        )
        assert len(logs) == 1
        assert logs[0]["level"] == "ERROR"  # error_rate > 0
        assert logs[0]["source"] == "prometheus"
        assert logs[0]["metadata"]["value"] == "0.15"

    def test_result_to_logs_up_status_down(self):
        result = {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"job": "summarizer-v2"},
                    "values": [[1705312320.0, "0"]],
                }
            ],
        }
        logs = PrometheusClient._result_to_logs(
            query_name="up_status",
            description="Target up/down status",
            promql="up",
            result=result,
            agent_name="summarizer-v2",
        )
        assert logs[0]["level"] == "ERROR"  # up=0 means down

    def test_healthy_metrics_no_error_signals(self):
        """All metrics healthy → no error signals."""
        result = {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"job": "summarizer-v2"},
                    "values": [[1705312320.0, "0"]],
                }
            ],
        }
        logs = PrometheusClient._result_to_logs(
            query_name="error_rate",
            description="HTTP 5xx error rate",
            promql="test",
            result=result,
            agent_name="summarizer-v2",
        )
        # error_rate=0 → INFO level
        assert logs[0]["level"] == "INFO"
        assert _has_error_signals(logs) is False


# ── Integration: API Endpoints ─────────────────────────────────────────


class TestHealthEndpoint:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestNormalizationEndpoint:
    def test_rejects_empty_body(self):
        resp = client.post("/api/v1/normalize", json={})
        assert resp.status_code == 422

    def test_rejects_missing_agent_name(self):
        resp = client.post(
            "/api/v1/normalize",
            json={"timestamp": "2025-01-15T10:32:00Z"},
        )
        assert resp.status_code == 422

    def test_rejects_missing_timestamp(self):
        resp = client.post(
            "/api/v1/normalize",
            json={"agent_name": "test"},
        )
        assert resp.status_code == 422

    def test_accepts_with_trace_id(self):
        """Validates shape only — LLM call requires API key."""
        req = NormalizationRequest(
            timestamp="2025-01-15T10:32:00Z",
            trace_id="trace-abc-123",
            agent_name="summarizer-v2",
        )
        assert req.trace_id == "trace-abc-123"

    def test_accepts_without_trace_id(self):
        """Validates shape only — Prometheus path."""
        req = NormalizationRequest(
            timestamp="2025-01-15T10:32:00Z",
            agent_name="summarizer-v2",
        )
        assert req.trace_id is None
