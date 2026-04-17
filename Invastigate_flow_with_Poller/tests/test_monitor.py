"""
Tests for the real-time monitor endpoints:
  POST /api/v1/monitor/investigate
  GET  /api/v1/monitor/stream/{trace_id}

Coverage:
  1. EventBus — subscribe/publish/unsubscribe, wildcard, queue-full drop
  2. POST /monitor/investigate — returns trace_id + stream_url
  3. GET /monitor/stream — SSE format, connected event, keepalive
  4. Full SSE flow — trigger pipeline, collect events, verify order/content
  5. Orchestrator event publishing — all 5 agent events emitted
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.event_bus import EventBus, get_event_bus


# ── Helpers ────────────────────────────────────────────────────────────

def make_request(agent_name: str = "test-agent", trace_id: str | None = None) -> dict:
    return {
        "agent_name": agent_name,
        "trace_id": trace_id or f"trace-{uuid.uuid4().hex[:8]}",
        "timestamp": "2025-01-15T10:00:00Z",
    }


# ════════════════════════════════════════════════════════════════════════
# 1. EventBus unit tests
# ════════════════════════════════════════════════════════════════════════

class TestEventBus:

    def test_subscribe_returns_unique_ids(self):
        bus = EventBus()
        id1, _ = bus.subscribe("trace-A")
        id2, _ = bus.subscribe("trace-A")
        assert id1 != id2

    def test_subscribe_returns_queue(self):
        bus = EventBus()
        sub_id, q = bus.subscribe("trace-A")
        assert isinstance(q, asyncio.Queue)

    @pytest.mark.asyncio
    async def test_publish_puts_to_queue(self):
        bus = EventBus()
        sub_id, q = bus.subscribe("trace-A")
        event = {"type": "test", "data": 42}
        await bus.publish("trace-A", event)
        received = q.get_nowait()
        assert received == event

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(self):
        bus = EventBus()
        _, q1 = bus.subscribe("trace-A")
        _, q2 = bus.subscribe("trace-A")
        event = {"type": "step_started"}
        await bus.publish("trace-A", event)
        assert q1.get_nowait() == event
        assert q2.get_nowait() == event

    @pytest.mark.asyncio
    async def test_publish_wrong_trace_id_not_received(self):
        bus = EventBus()
        _, q = bus.subscribe("trace-A")
        await bus.publish("trace-B", {"type": "irrelevant"})
        assert q.empty()

    def test_unsubscribe_removes_subscriber(self):
        bus = EventBus()
        sub_id, _ = bus.subscribe("trace-A")
        bus.unsubscribe("trace-A", sub_id)
        # No subscribers left for trace-A
        assert "trace-A" not in bus._subs

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_receiving(self):
        bus = EventBus()
        sub_id, q = bus.subscribe("trace-A")
        bus.unsubscribe("trace-A", sub_id)
        await bus.publish("trace-A", {"type": "dropped"})
        assert q.empty()

    @pytest.mark.asyncio
    async def test_wildcard_subscriber_receives_all(self):
        bus = EventBus()
        _, wq = bus.subscribe("*")
        await bus.publish("trace-A", {"type": "a"})
        await bus.publish("trace-B", {"type": "b"})
        events = [wq.get_nowait(), wq.get_nowait()]
        assert {e["type"] for e in events} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_queue_full_drops_event_without_blocking(self):
        bus = EventBus()
        sub_id, q = bus.subscribe("trace-A")
        # Fill the queue
        for i in range(q.maxsize):
            q.put_nowait({"i": i})
        # This should NOT raise
        await bus.publish("trace-A", {"type": "overflow"})
        # Queue still has exactly maxsize items (overflow dropped)
        assert q.full()

    @pytest.mark.asyncio
    async def test_publish_to_nonexistent_trace_noop(self):
        bus = EventBus()
        # Should not raise
        await bus.publish("nonexistent", {"type": "ghost"})

    def test_unsubscribe_nonexistent_noop(self):
        bus = EventBus()
        bus.unsubscribe("ghost-trace", "ghost-sub-id")  # should not raise


# ════════════════════════════════════════════════════════════════════════
# 2. POST /api/v1/monitor/investigate
# ════════════════════════════════════════════════════════════════════════

class TestMonitorInvestigateEndpoint:

    def test_returns_trace_id_and_stream_url(self):
        req = make_request(trace_id="mon-trace-001")
        with TestClient(app) as client:
            resp = client.post("/api/v1/monitor/investigate", json=req)
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace_id"] == "mon-trace-001"
        assert data["stream_url"] == "/api/v1/monitor/stream/mon-trace-001"

    def test_returns_200_immediately(self):
        """Endpoint must return before the background task finishes."""
        req = make_request()
        with TestClient(app) as client:
            resp = client.post("/api/v1/monitor/investigate", json=req)
        assert resp.status_code == 200

    def test_missing_agent_name_returns_422(self):
        with TestClient(app) as client:
            resp = client.post("/api/v1/monitor/investigate", json={
                "trace_id": "mon-002",
                "timestamp": "2025-01-15T10:00:00Z",
            })
        assert resp.status_code == 422

    def test_missing_timestamp_returns_422(self):
        with TestClient(app) as client:
            resp = client.post("/api/v1/monitor/investigate", json={
                "agent_name": "test-agent",
                "trace_id": "mon-003",
            })
        assert resp.status_code == 422

    def test_missing_trace_id_returns_422(self):
        with TestClient(app) as client:
            resp = client.post("/api/v1/monitor/investigate", json={
                "agent_name": "test-agent",
                "timestamp": "2025-01-15T10:00:00Z",
            })
        assert resp.status_code == 422


# ════════════════════════════════════════════════════════════════════════
# 3. GET /api/v1/monitor/stream — SSE format
# ════════════════════════════════════════════════════════════════════════

class TestMonitorStreamEndpoint:

    @pytest.mark.asyncio
    async def test_returns_event_stream_content_type(self):
        """SSE endpoint must respond with text/event-stream content type."""
        bus = get_event_bus()
        trace_id = f"ct-{uuid.uuid4().hex[:6]}"

        async def publish_done():
            await asyncio.sleep(0.05)
            await bus.publish(trace_id, {"type": "pipeline_completed", "trace_id": trace_id, "completed": True, "total_processing_time_ms": 10})

        async def check_headers():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.stream("GET", f"/api/v1/monitor/stream/{trace_id}") as resp:
                    assert resp.headers["content-type"].startswith("text/event-stream")
                    # Drain until stream closes
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            evt = json.loads(line.removeprefix("data: "))
                            if evt.get("type") == "pipeline_completed":
                                break

        await asyncio.gather(check_headers(), publish_done())

    @pytest.mark.asyncio
    async def test_first_event_is_connected(self):
        """First SSE event must be type=connected with the correct trace_id."""
        bus = get_event_bus()
        trace_id = f"conn-{uuid.uuid4().hex[:6]}"
        received: list[dict] = []

        async def publish_done():
            await asyncio.sleep(0.05)
            await bus.publish(trace_id, {"type": "pipeline_completed", "trace_id": trace_id, "completed": True, "total_processing_time_ms": 10})

        async def collect():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.stream("GET", f"/api/v1/monitor/stream/{trace_id}") as resp:
                    # Single loop — collect all events until terminal
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            evt = json.loads(line.removeprefix("data: "))
                            received.append(evt)
                            if evt.get("type") == "pipeline_completed":
                                break

        await asyncio.gather(collect(), publish_done())
        assert len(received) >= 1
        assert received[0]["type"] == "connected"
        assert received[0]["trace_id"] == trace_id

    @pytest.mark.asyncio
    async def test_cache_control_no_cache(self):
        """SSE response must have Cache-Control: no-cache."""
        bus = get_event_bus()
        trace_id = f"cc-{uuid.uuid4().hex[:6]}"

        async def publish_done():
            await asyncio.sleep(0.05)
            await bus.publish(trace_id, {"type": "pipeline_completed", "trace_id": trace_id, "completed": True, "total_processing_time_ms": 10})

        async def check():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.stream("GET", f"/api/v1/monitor/stream/{trace_id}") as resp:
                    assert resp.headers.get("cache-control") == "no-cache"
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            evt = json.loads(line.removeprefix("data: "))
                            if evt.get("type") == "pipeline_completed":
                                break

        await asyncio.gather(check(), publish_done())

    @pytest.mark.asyncio
    async def test_published_event_arrives_at_stream(self):
        """Publish an event via the bus and verify it appears in the SSE stream."""
        bus = get_event_bus()
        trace_id = f"stream-live-{uuid.uuid4().hex[:6]}"
        received: list[dict] = []

        async def collect():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.stream("GET", f"/api/v1/monitor/stream/{trace_id}") as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        event = json.loads(line.removeprefix("data: "))
                        received.append(event)
                        if event["type"] in ("pipeline_completed", "error"):
                            break

        # Give the SSE connection a moment to establish, then publish
        async def publish_sequence():
            await asyncio.sleep(0.05)  # let collect() subscribe first
            await bus.publish(trace_id, {"type": "pipeline_started", "trace_id": trace_id, "agent_name": "t"})
            await bus.publish(trace_id, {"type": "step_started", "agent": "normalization", "step": 1})
            await bus.publish(trace_id, {"type": "pipeline_completed", "trace_id": trace_id, "completed": True, "total_processing_time_ms": 100})

        await asyncio.gather(collect(), publish_sequence())

        types = [e["type"] for e in received]
        assert "connected" in types
        assert "pipeline_started" in types
        assert "step_started" in types
        assert "pipeline_completed" in types

    @pytest.mark.asyncio
    async def test_stream_closes_after_pipeline_completed(self):
        """SSE generator exits when it receives pipeline_completed."""
        bus = get_event_bus()
        trace_id = f"stream-done-{uuid.uuid4().hex[:6]}"
        received: list[dict] = []

        async def collect():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.stream("GET", f"/api/v1/monitor/stream/{trace_id}") as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            received.append(json.loads(line.removeprefix("data: ")))
                            if received[-1]["type"] == "pipeline_completed":
                                break

        async def publish():
            await asyncio.sleep(0.05)
            await bus.publish(trace_id, {"type": "pipeline_completed", "trace_id": trace_id, "completed": True, "total_processing_time_ms": 50})

        await asyncio.gather(collect(), publish())
        assert received[-1]["type"] == "pipeline_completed"

    @pytest.mark.asyncio
    async def test_stream_closes_after_error_event(self):
        bus = get_event_bus()
        trace_id = f"stream-err-{uuid.uuid4().hex[:6]}"
        received: list[dict] = []

        async def collect():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.stream("GET", f"/api/v1/monitor/stream/{trace_id}") as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            received.append(json.loads(line.removeprefix("data: ")))
                            if received[-1]["type"] == "error":
                                break

        async def publish():
            await asyncio.sleep(0.05)
            await bus.publish(trace_id, {"type": "error", "message": "boom"})

        await asyncio.gather(collect(), publish())
        assert received[-1]["type"] == "error"
        assert received[-1]["message"] == "boom"


# ════════════════════════════════════════════════════════════════════════
# 4. Orchestrator event publishing — verify event types & order
# ════════════════════════════════════════════════════════════════════════

class TestOrchestratorEventPublishing:
    """
    Mock all 5 agents to return minimal valid responses.
    Verify the orchestrator publishes the expected events to the bus.
    """

    def _make_norm_resp(self):
        from app.models.normalization import NormalizationResponse, NormalizedIncident, ErrorType, Entities, DataSource
        incident = NormalizedIncident(
            error_type=ErrorType.AI_AGENT,
            error_summary="LLM returned empty response",
            signals=["empty_response"],
            confidence=0.9,
            entities=Entities(agent_id="ag-1", service="svc-1"),
            timestamp="2025-01-15T10:00:00Z",
        )
        return NormalizationResponse(
            incident=incident,
            data_source=DataSource.LANGFUSE,
            raw_log_count=5,
            processing_time_ms=100,
        )

    def _make_norm_resp_no_error(self):
        from app.models.normalization import NormalizationResponse, NormalizedIncident, ErrorType, Entities, DataSource
        incident = NormalizedIncident(
            error_type=ErrorType.NO_ERROR,
            error_summary="No error detected",
            signals=[],
            confidence=0.5,
            entities=Entities(),
            timestamp="2025-01-15T10:00:00Z",
        )
        return NormalizationResponse(
            incident=incident,
            data_source=DataSource.PROMETHEUS,
            raw_log_count=3,
            processing_time_ms=50,
        )

    def _make_corr_resp(self):
        from app.models.correlation import (
            CorrelationResponse, CorrelationResult, RootCauseCandidate, AnalysisDomain,
        )
        corr = CorrelationResult(
            correlation_chain=["svc-A → svc-B"],
            root_cause_candidate=RootCauseCandidate(component="svc-A", confidence=0.9, reason="test"),
            analysis_target=AnalysisDomain.AGENT,
        )
        return CorrelationResponse(correlation=corr, data_sources=["langfuse"], total_logs_analyzed=10, processing_time_ms=200)

    def _make_ea_resp(self):
        from app.models.error_analysis import (
            ErrorAnalysisResponse, ErrorAnalysisResult, ErrorDetail,
            ErrorCategory, ErrorSeverity, AnalysisDomain,
        )
        err = ErrorDetail(
            error_id="ERR-001", category=ErrorCategory.LLM_FAILURE,
            severity=ErrorSeverity.HIGH, component="svc-A",
            error_message="LLM timeout", timestamp="2025-01-15T10:00:00Z",
            evidence="log line 1", source="langfuse",
        )
        analysis = ErrorAnalysisResult(
            analysis_summary="One error found",
            analysis_target=AnalysisDomain.AGENT,
            errors=[err],
            confidence=0.85,
        )
        return ErrorAnalysisResponse(
            analysis=analysis, rca_target=AnalysisDomain.AGENT,
            data_sources=["langfuse"], total_logs_analyzed=5,
            processing_time_ms=300,
        )

    def _make_rca_resp(self):
        from app.models.rca import (
            RCAResponse, RCAResult, RootCause, RootCauseCategory,
            FiveWhyAnalysis, WhyStep, CausalLink, CausalLinkType,
        )
        whys = [
            WhyStep(
                step=i, question=f"Why did step {i} fail?",
                answer=f"Because of cause {i}", evidence=f"log evidence {i}",
                component="svc-A",
            )
            for i in range(1, 6)
        ]
        rca = RCAResult(
            rca_summary="LLM provider was unavailable",
            root_cause=RootCause(
                category=RootCauseCategory.LLM_PROVIDER,
                component="svc-A",
                description="LLM API returned 503 Service Unavailable",
                evidence=["503 error in logs"],
                confidence=0.88,
            ),
            causal_chain=[
                CausalLink(
                    source_event="LLM provider returned 503",
                    target_event="svc-A request failed",
                    link_type=CausalLinkType.DIRECT_CAUSE,
                    evidence="503 in access log",
                )
            ],
            five_why_analysis=FiveWhyAnalysis(
                problem_statement="LLM returned 503 on every request",
                whys=whys,
                fundamental_root_cause="Missing fallback LLM provider",
            ),
            confidence=0.88,
        )
        return RCAResponse(
            rca=rca, data_sources=["langfuse"],
            total_logs_analyzed=5, processing_time_ms=400,
        )

    def _make_rec_resp(self):
        from app.models.recommendation import RecommendationResponse, RecommendationResult, Recommendation, Priority
        rec = Recommendation(
            title="Add retry logic",
            description="Retry on 503",
            priority=Priority.HIGH,
            affected_component="svc-A",
            implementation_steps=["step 1"],
        )
        result = RecommendationResult(
            recommendations=[rec],
            executive_summary="Retry needed",
            confidence=0.9,
        )
        return RecommendationResponse(recommendations=result, processing_time_ms=150)

    @pytest.mark.asyncio
    async def test_pipeline_emits_all_expected_event_types(self):
        """
        Patch all 5 agents + store, run orchestrator.investigate(),
        collect all bus events, verify the full sequence.
        """
        from app.agents.orchestrator import Orchestrator

        trace_id = f"orch-ev-{uuid.uuid4().hex[:6]}"
        bus = get_event_bus()
        sub_id, queue = bus.subscribe(trace_id)

        norm_resp = self._make_norm_resp()
        corr_resp = self._make_corr_resp()
        ea_resp = self._make_ea_resp()
        rca_resp = self._make_rca_resp()
        rec_resp = self._make_rec_resp()

        with (
            patch("app.agents.orchestrator.NormalizationAgent.normalize", new_callable=AsyncMock, return_value=norm_resp),
            patch("app.agents.orchestrator.CorrelationAgent.correlate", new_callable=AsyncMock, return_value=corr_resp),
            patch("app.agents.orchestrator.ErrorAnalysisAgent.analyze", new_callable=AsyncMock, return_value=ea_resp),
            patch("app.agents.orchestrator.RCAAgent.analyze_root_cause", new_callable=AsyncMock, return_value=rca_resp),
            patch("app.agents.orchestrator.RecommendationAgent.recommend", new_callable=AsyncMock, return_value=rec_resp),
            patch("app.agents.orchestrator.TraceStore.create_trace", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.save_agent_io", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.complete_trace", new_callable=AsyncMock),
        ):
            orch = Orchestrator()
            await orch.investigate(MagicMock(
                trace_id=trace_id,
                agent_name="test-agent",
                timestamp="2025-01-15T10:00:00Z",
            ))

        # Drain the queue
        events: list[dict[str, Any]] = []
        while not queue.empty():
            events.append(queue.get_nowait())

        bus.unsubscribe(trace_id, sub_id)

        types = [e["type"] for e in events]
        print(f"Events: {types}")  # helpful for debugging

        assert "pipeline_started" in types
        assert types.count("step_started") == 5
        assert types.count("step_completed") == 5
        assert "pipeline_completed" in types

    @pytest.mark.asyncio
    async def test_pipeline_started_event_has_correct_fields(self):
        from app.agents.orchestrator import Orchestrator

        trace_id = f"orch-ps-{uuid.uuid4().hex[:6]}"
        bus = get_event_bus()
        sub_id, queue = bus.subscribe(trace_id)

        norm_resp = self._make_norm_resp_no_error()

        with (
            patch("app.agents.orchestrator.NormalizationAgent.normalize", new_callable=AsyncMock, return_value=norm_resp),
            patch("app.agents.orchestrator.TraceStore.create_trace", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.save_agent_io", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.complete_trace", new_callable=AsyncMock),
        ):
            orch = Orchestrator()
            await orch.investigate(MagicMock(
                trace_id=trace_id, agent_name="my-agent", timestamp="2025-01-15T10:00:00Z",
            ))

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        bus.unsubscribe(trace_id, sub_id)

        started = next(e for e in events if e["type"] == "pipeline_started")
        assert started["trace_id"] == trace_id
        assert started["agent_name"] == "my-agent"
        assert started["timestamp"] == "2025-01-15T10:00:00Z"

    @pytest.mark.asyncio
    async def test_step_completed_has_input_output_and_metadata(self):
        from app.agents.orchestrator import Orchestrator

        trace_id = f"orch-sc-{uuid.uuid4().hex[:6]}"
        bus = get_event_bus()
        sub_id, queue = bus.subscribe(trace_id)

        norm_resp = self._make_norm_resp()
        corr_resp = self._make_corr_resp()
        ea_resp = self._make_ea_resp()
        rca_resp = self._make_rca_resp()
        rec_resp = self._make_rec_resp()

        with (
            patch("app.agents.orchestrator.NormalizationAgent.normalize", new_callable=AsyncMock, return_value=norm_resp),
            patch("app.agents.orchestrator.CorrelationAgent.correlate", new_callable=AsyncMock, return_value=corr_resp),
            patch("app.agents.orchestrator.ErrorAnalysisAgent.analyze", new_callable=AsyncMock, return_value=ea_resp),
            patch("app.agents.orchestrator.RCAAgent.analyze_root_cause", new_callable=AsyncMock, return_value=rca_resp),
            patch("app.agents.orchestrator.RecommendationAgent.recommend", new_callable=AsyncMock, return_value=rec_resp),
            patch("app.agents.orchestrator.TraceStore.create_trace", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.save_agent_io", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.complete_trace", new_callable=AsyncMock),
        ):
            orch = Orchestrator()
            await orch.investigate(MagicMock(
                trace_id=trace_id, agent_name="test", timestamp="2025-01-15T10:00:00Z",
            ))

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        bus.unsubscribe(trace_id, sub_id)

        # Check correlation step_completed has data_sources and logs_count
        corr_done = next(e for e in events if e["type"] == "step_completed" and e["agent"] == "correlation")
        assert corr_done["data_sources"] == ["langfuse"]
        assert corr_done["logs_count"] == 10
        assert corr_done["confidence"] == pytest.approx(0.9)
        assert "input" in corr_done
        assert "output" in corr_done
        assert corr_done["processing_time_ms"] >= 0

        # Check error_analysis step_completed
        ea_done = next(e for e in events if e["type"] == "step_completed" and e["agent"] == "error_analysis")
        assert ea_done["logs_count"] == 5
        assert ea_done["confidence"] == pytest.approx(0.85)

        # Check rca step_completed
        rca_done = next(e for e in events if e["type"] == "step_completed" and e["agent"] == "rca")
        assert rca_done["confidence"] == pytest.approx(0.88)

    @pytest.mark.asyncio
    async def test_pipeline_completed_event_has_total_time(self):
        from app.agents.orchestrator import Orchestrator

        trace_id = f"orch-pc-{uuid.uuid4().hex[:6]}"
        bus = get_event_bus()
        sub_id, queue = bus.subscribe(trace_id)

        norm_resp = self._make_norm_resp_no_error()  # NO_ERROR — 1-step pipeline

        with (
            patch("app.agents.orchestrator.NormalizationAgent.normalize", new_callable=AsyncMock, return_value=norm_resp),
            patch("app.agents.orchestrator.TraceStore.create_trace", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.save_agent_io", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.complete_trace", new_callable=AsyncMock),
        ):
            orch = Orchestrator()
            await orch.investigate(MagicMock(
                trace_id=trace_id, agent_name="test", timestamp="2025-01-15T10:00:00Z",
            ))

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        bus.unsubscribe(trace_id, sub_id)

        completed = next(e for e in events if e["type"] == "pipeline_completed")
        assert completed["trace_id"] == trace_id
        assert completed["completed"] is True
        assert completed["total_processing_time_ms"] >= 0
        assert isinstance(completed["steps"], list)

    @pytest.mark.asyncio
    async def test_step_failed_event_emitted_on_agent_exception(self):
        from app.agents.orchestrator import Orchestrator

        trace_id = f"orch-fail-{uuid.uuid4().hex[:6]}"
        bus = get_event_bus()
        sub_id, queue = bus.subscribe(trace_id)

        norm_resp = self._make_norm_resp()

        with (
            patch("app.agents.orchestrator.NormalizationAgent.normalize", new_callable=AsyncMock, return_value=norm_resp),
            patch("app.agents.orchestrator.CorrelationAgent.correlate", new_callable=AsyncMock, side_effect=RuntimeError("Correlation failed")),
            patch("app.agents.orchestrator.TraceStore.create_trace", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.save_agent_io", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.complete_trace", new_callable=AsyncMock),
        ):
            orch = Orchestrator()
            await orch.investigate(MagicMock(
                trace_id=trace_id, agent_name="test", timestamp="2025-01-15T10:00:00Z",
            ))

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        bus.unsubscribe(trace_id, sub_id)

        types = [e["type"] for e in events]
        assert "step_failed" in types

        failed_event = next(e for e in events if e["type"] == "step_failed")
        assert failed_event["agent"] == "correlation"
        assert "Correlation failed" in failed_event["error"]

        # pipeline_completed still emitted even on failure
        assert "pipeline_completed" in types

    @pytest.mark.asyncio
    async def test_event_order_is_correct(self):
        """Verify events arrive in the correct pipeline order."""
        from app.agents.orchestrator import Orchestrator

        trace_id = f"orch-order-{uuid.uuid4().hex[:6]}"
        bus = get_event_bus()
        sub_id, queue = bus.subscribe(trace_id)

        norm_resp = self._make_norm_resp()
        corr_resp = self._make_corr_resp()
        ea_resp = self._make_ea_resp()
        rca_resp = self._make_rca_resp()
        rec_resp = self._make_rec_resp()

        with (
            patch("app.agents.orchestrator.NormalizationAgent.normalize", new_callable=AsyncMock, return_value=norm_resp),
            patch("app.agents.orchestrator.CorrelationAgent.correlate", new_callable=AsyncMock, return_value=corr_resp),
            patch("app.agents.orchestrator.ErrorAnalysisAgent.analyze", new_callable=AsyncMock, return_value=ea_resp),
            patch("app.agents.orchestrator.RCAAgent.analyze_root_cause", new_callable=AsyncMock, return_value=rca_resp),
            patch("app.agents.orchestrator.RecommendationAgent.recommend", new_callable=AsyncMock, return_value=rec_resp),
            patch("app.agents.orchestrator.TraceStore.create_trace", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.save_agent_io", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.complete_trace", new_callable=AsyncMock),
        ):
            orch = Orchestrator()
            await orch.investigate(MagicMock(
                trace_id=trace_id, agent_name="test", timestamp="2025-01-15T10:00:00Z",
            ))

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        bus.unsubscribe(trace_id, sub_id)

        types = [e["type"] for e in events]

        # First event = pipeline_started
        assert types[0] == "pipeline_started"
        # Last event = pipeline_completed
        assert types[-1] == "pipeline_completed"

        # For each agent, step_started must come before step_completed
        agents_seen = []
        for e in events:
            if e["type"] == "step_started":
                agents_seen.append(("start", e["agent"]))
            elif e["type"] == "step_completed":
                agents_seen.append(("done", e["agent"]))

        # Build (start, done) pairs in order
        expected_agents = ["normalization", "correlation", "error_analysis", "rca", "recommendation"]
        start_order = [a for s, a in agents_seen if s == "start"]
        done_order = [a for s, a in agents_seen if s == "done"]
        assert start_order == expected_agents
        assert done_order == expected_agents

    @pytest.mark.asyncio
    async def test_no_error_pipeline_stops_after_normalization(self):
        """NO_ERROR short-circuit: only step 1 fires, no corr/ea/rca/rec events."""
        from app.agents.orchestrator import Orchestrator

        trace_id = f"orch-noe-{uuid.uuid4().hex[:6]}"
        bus = get_event_bus()
        sub_id, queue = bus.subscribe(trace_id)

        norm_resp = self._make_norm_resp_no_error()

        with (
            patch("app.agents.orchestrator.NormalizationAgent.normalize", new_callable=AsyncMock, return_value=norm_resp),
            patch("app.agents.orchestrator.TraceStore.create_trace", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.save_agent_io", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.complete_trace", new_callable=AsyncMock),
        ):
            orch = Orchestrator()
            await orch.investigate(MagicMock(
                trace_id=trace_id, agent_name="test", timestamp="2025-01-15T10:00:00Z",
            ))

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        bus.unsubscribe(trace_id, sub_id)

        agent_names = [e.get("agent") for e in events if e.get("agent")]
        assert "correlation" not in agent_names
        assert "error_analysis" not in agent_names
        assert "rca" not in agent_names
        assert "recommendation" not in agent_names
        assert any(e["type"] == "pipeline_completed" for e in events)


# ════════════════════════════════════════════════════════════════════════
# 5. get_event_bus singleton
# ════════════════════════════════════════════════════════════════════════

class TestEventBusSingleton:

    def test_get_event_bus_returns_same_instance(self):
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2

    @pytest.mark.asyncio
    async def test_orchestrator_and_monitor_share_same_bus(self):
        """Events from the orchestrator are visible to the monitor SSE route."""
        from app.agents.orchestrator import Orchestrator

        bus = get_event_bus()
        trace_id = f"shared-bus-{uuid.uuid4().hex[:6]}"
        sub_id, queue = bus.subscribe(trace_id)

        norm_resp = self._make_norm_resp_no_error()

        with (
            patch("app.agents.orchestrator.NormalizationAgent.normalize", new_callable=AsyncMock, return_value=norm_resp),
            patch("app.agents.orchestrator.TraceStore.create_trace", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.save_agent_io", new_callable=AsyncMock),
            patch("app.agents.orchestrator.TraceStore.complete_trace", new_callable=AsyncMock),
        ):
            orch = Orchestrator()
            await orch.investigate(MagicMock(
                trace_id=trace_id, agent_name="test", timestamp="2025-01-15T10:00:00Z",
            ))

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        bus.unsubscribe(trace_id, sub_id)

        assert any(e["type"] == "pipeline_started" for e in events)

    def _make_norm_resp_no_error(self):
        from app.models.normalization import NormalizationResponse, NormalizedIncident, ErrorType, Entities, DataSource
        incident = NormalizedIncident(
            error_type=ErrorType.NO_ERROR,
            error_summary="No error",
            signals=[],
            confidence=0.5,
            entities=Entities(),
            timestamp="2025-01-15T10:00:00Z",
        )
        return NormalizationResponse(
            incident=incident,
            data_source=DataSource.PROMETHEUS,
            raw_log_count=0,
            processing_time_ms=50,
        )
