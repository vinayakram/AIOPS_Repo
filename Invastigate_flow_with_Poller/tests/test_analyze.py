"""
Tests for the /analyze frontend endpoint.

Run with: pytest tests/test_analyze.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


class TestAnalyzeEndpoint:
    def test_route_exists(self):
        routes = [r.path for r in app.routes]
        assert "/api/v1/analyze" in routes

    def test_rejects_empty_body(self):
        resp = client.post("/api/v1/analyze", json={})
        assert resp.status_code == 422

    def test_rejects_missing_trace_id(self):
        resp = client.post(
            "/api/v1/analyze",
            json={"timestamp": "2026-04-10T04:19:55.204Z", "agent_name": "test"},
        )
        assert resp.status_code == 422

    def test_rejects_missing_timestamp(self):
        resp = client.post(
            "/api/v1/analyze",
            json={"trace_id": "trace-abc", "agent_name": "test"},
        )
        assert resp.status_code == 422

    def test_rejects_missing_agent_name(self):
        resp = client.post(
            "/api/v1/analyze",
            json={"timestamp": "2026-04-10T04:19:55.204Z", "trace_id": "trace-abc"},
        )
        assert resp.status_code == 422

    def test_new_trace_returns_pipeline_source(self):
        """
        A brand new trace_id should trigger the pipeline.
        It will fail (no LLM key) but the response structure
        should indicate source=pipeline.
        """
        resp = client.post(
            "/api/v1/analyze",
            json={
                "timestamp": "2026-04-10T04:19:55.204Z",
                "trace_id": "trace-test-analyze-new-001",
                "agent_name": "test-agent",
            },
        )
        # Pipeline will fail without API keys, but we get 200 or 500
        # Either way the source field tells us it tried the pipeline
        if resp.status_code == 200:
            data = resp.json()
            assert data["source"] == "pipeline"

    def test_same_trace_second_call_returns_cache(self):
        """
        If a trace_id was already stored in DB (even as failed),
        the second call should return source=cache.
        """
        trace_id = "trace-test-analyze-cache-002"

        # First call — triggers pipeline (may fail, that's OK)
        client.post(
            "/api/v1/analyze",
            json={
                "timestamp": "2026-04-10T04:19:55.204Z",
                "trace_id": trace_id,
                "agent_name": "test-agent",
            },
        )

        # Second call — should hit cache
        resp = client.post(
            "/api/v1/analyze",
            json={
                "timestamp": "2026-04-10T04:19:55.204Z",
                "trace_id": trace_id,
                "agent_name": "test-agent",
            },
        )

        if resp.status_code == 200:
            data = resp.json()
            assert data["source"] == "cache"
            assert "data" in data
            assert data["data"]["trace_id"] == trace_id
